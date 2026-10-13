"""Data Table Manager: backend for managing Amazon Connect data tables and reading contact flows.

One Lambda, one operation per call: {"operation": "<Name>", ...inputs}. Operations follow Vanja's spec
(docs/data-table-manager.md), adapted to the real Connect APIs:

  ListDataTables            {}                                  -> [{DataTableId, DataTableArn, Name}]
  DescribeDataTable         {DataTableIdentifier}               -> table + Attributes[] (+ RowCount)
  GetDataTableRows          {DataTableIdentifier}               -> rows pivoted for a grid (UI "View" screen)
  CreateDataTable           {TableName, Description?, Attributes[], TimeZone?, ValueLockLevel?}
  UpdateDataTable           {DataTableIdentifier, Operations[{PrimaryKeyValues, AttributeValues}], Mode?}
                            upsert (default) or create_only (never overwrite existing values: used for seeding)
  ListContactFlows          {ContactFlowType?}
  DescribeContactFlow       {ContactFlowIdentifier}             (name or ARN/id)
  IdentifyTableRequirements {ContactFlowContent, ContactFlowName?}  static analysis, no AWS calls

The instance is fixed by the environment (INSTANCE_ID): a tenant's Lambda only manages its own instance.
"""
from __future__ import annotations

import json
import os
import re
from collections import OrderedDict

import boto3
from botocore.exceptions import ClientError

INSTANCE_ID = os.environ.get("INSTANCE_ID", "")
DEFAULT_TZ = os.environ.get("DEFAULT_TIME_ZONE", "America/Toronto")
VALUE_TYPES = {"TEXT", "NUMBER", "BOOLEAN", "TEXT_LIST", "NUMBER_LIST"}
BATCH = 25
_connect = None


class UserError(Exception):
    """Bad input: returned to the caller as {"error": ...} instead of raising."""


def connect():
    global _connect
    if _connect is None:
        _connect = boto3.client("connect")
    return _connect


def _pages(op, key, **kw):
    out, token = [], None
    while True:
        r = getattr(connect(), op)(**kw, **({"NextToken": token} if token else {}))
        out += r.get(key, [])
        token = r.get("NextToken")
        if not token:
            return out


def _instance(event):
    inst = event.get("InstanceId") or INSTANCE_ID
    if INSTANCE_ID and inst.rsplit("/", 1)[-1] != INSTANCE_ID.rsplit("/", 1)[-1]:
        raise UserError("this function manages only its own Connect instance")
    return INSTANCE_ID or inst


# ------------------------------------------------------------------ data tables
def list_data_tables(event):
    rows = _pages("list_data_tables", "DataTableSummaryList", InstanceId=_instance(event))
    return {"DataTables": [{"DataTableId": t.get("Id"), "DataTableArn": t.get("Arn"), "Name": t.get("Name")} for t in rows]}


def _resolve_table(event):
    ident = (event.get("DataTableIdentifier") or "").strip()
    if not ident:
        raise UserError("DataTableIdentifier is required")
    if ident.startswith("arn:") or re.fullmatch(r"[0-9a-f-]{36}", ident):
        return ident
    for t in list_data_tables(event)["DataTables"]:
        if t["Name"].lower() == ident.lower():
            return t["DataTableId"]
    raise UserError(f"no data table named {ident!r}")


def _attributes(inst, table_id):
    return _pages("list_data_table_attributes", "Attributes", InstanceId=inst, DataTableId=table_id)


def describe_data_table(event):
    inst, tid = _instance(event), _resolve_table(event)
    t = connect().describe_data_table(InstanceId=inst, DataTableId=tid)["DataTable"]
    attrs = [{"Name": a["Name"], "ValueType": a["ValueType"], "Primary": bool(a.get("Primary")),
              "Validation": a.get("Validation") or {}, "Description": a.get("Description", "")} for a in _attributes(inst, tid)]
    attrs.sort(key=lambda a: (not a["Primary"], a["Name"].lower()))
    return {"DataTableId": t["Id"], "DataTableArn": t["Arn"], "Name": t["Name"], "Description": t.get("Description", ""),
            "TimeZone": t.get("TimeZone"), "ValueLockLevel": t.get("ValueLockLevel"), "Attributes": attrs}


def _values(inst, tid):
    return _pages("list_data_table_values", "Values", InstanceId=inst, DataTableId=tid)


def _pk(primary_values):
    return tuple(sorted((p["AttributeName"], p["Value"]) for p in primary_values or []))


def get_data_table_rows(event):
    """Pivot the value list into rows: one dict per primary-key combination (the grid the UI shows)."""
    d = describe_data_table(event)
    rows: "OrderedDict[tuple, dict]" = OrderedDict()
    for v in _values(_instance(event), d["DataTableId"]):
        key = _pk(v.get("PrimaryValues"))
        row = rows.setdefault(key, dict(key))
        row[v["AttributeName"]] = v.get("Value")
    return {"DataTable": {k: d[k] for k in ("DataTableId", "Name", "Description")},
            "Columns": [{"Name": a["Name"], "ValueType": a["ValueType"], "Primary": a["Primary"]} for a in d["Attributes"]],
            "Rows": list(rows.values()), "RowCount": len(rows)}


def _check_attributes(attrs):
    if not attrs:
        raise UserError("Attributes must not be empty")
    names = set()
    for a in attrs:
        if a.get("ValueType", "TEXT") not in VALUE_TYPES:
            raise UserError(f"attribute {a.get('Name')!r}: ValueType must be one of {sorted(VALUE_TYPES)}")
        n = (a.get("Name") or "").strip()
        if not n or n.lower() in names:
            raise UserError(f"attribute names must be present and unique (case-insensitive): {n!r}")
        names.add(n.lower())


def create_data_table(event):
    inst, attrs = _instance(event), event.get("Attributes") or []
    _check_attributes(attrs)
    name = (event.get("TableName") or "").strip()
    if not name:
        raise UserError("TableName is required")
    kw = {"InstanceId": inst, "Name": name, "Status": "PUBLISHED",
          "TimeZone": event.get("TimeZone") or DEFAULT_TZ, "ValueLockLevel": event.get("ValueLockLevel") or "PRIMARY_VALUE"}
    if event.get("Description"):
        kw["Description"] = event["Description"]
    t = connect().create_data_table(**kw)
    created = []
    for a in sorted(attrs, key=lambda a: not a.get("Primary")):  # primary attributes first
        akw = {"InstanceId": inst, "DataTableId": t["Id"], "Name": a["Name"].strip(),
               "ValueType": a.get("ValueType", "TEXT"), "Primary": bool(a.get("Primary"))}
        for k in ("Validation", "Description"):
            if a.get(k):
                akw[k] = a[k]
        connect().create_data_table_attribute(**akw)
        created.append(akw["Name"])
    return {"DataTableArn": t["Arn"], "DataTableId": t["Id"], "AttributesCreated": created}


def update_data_table(event):
    """Upsert rows. Connect has no native upsert: list current values once, then create missing ones and update
    existing ones with their lock versions. Mode create_only leaves existing values untouched (safe seeding)."""
    inst, tid = _instance(event), _resolve_table(event)
    mode = event.get("Mode", "upsert")
    ops = event.get("Operations") or []
    if not ops:
        raise UserError("Operations must not be empty")
    attrs = {a["Name"]: a for a in _attributes(inst, tid)}
    primaries = sorted(n for n, a in attrs.items() if a.get("Primary"))
    existing = {(_pk(v.get("PrimaryValues")), v["AttributeName"]): v for v in _values(inst, tid)}
    creates, updates, failures, skipped = [], [], [], 0
    for i, op in enumerate(ops):
        pkv = {k: str(v) for k, v in (op.get("PrimaryKeyValues") or {}).items()}
        if sorted(pkv) != primaries:
            failures.append({"Index": i, "Message": f"PrimaryKeyValues must have exactly {primaries}"})
            continue
        unknown = [k for k in (op.get("AttributeValues") or {}) if k not in attrs or k in primaries]
        if unknown:
            failures.append({"Index": i, "Message": f"unknown or primary attributes in AttributeValues: {unknown}"})
            continue
        pv = [{"AttributeName": k, "Value": v} for k, v in sorted(pkv.items())]
        for name, value in (op.get("AttributeValues") or {}).items():
            value = json.dumps(value) if isinstance(value, (list, dict)) else str(value).lower() if isinstance(value, bool) else str(value)
            cur = existing.get((_pk(pv), name))
            if cur is None:
                creates.append({"AttributeName": name, "PrimaryValues": pv, "Value": value})
            elif mode == "create_only" or cur.get("Value") == value:
                skipped += 1
            else:
                updates.append({"AttributeName": name, "PrimaryValues": pv, "Value": value, "LockVersion": cur["LockVersion"]})
    ok = 0
    for call, items in (("batch_create_data_table_value", creates), ("batch_update_data_table_value", updates)):
        for j in range(0, len(items), BATCH):
            r = getattr(connect(), call)(InstanceId=inst, DataTableId=tid, Values=items[j:j + BATCH])
            ok += len(r.get("Successful", []))
            failures += [{"AttributeName": f.get("AttributeName"), "PrimaryValues": f.get("PrimaryValues"),
                          "Message": f.get("Message")} for f in r.get("Failed", [])]
    return {"SuccessCount": ok, "Created": len(creates), "Updated": len(updates), "Unchanged": skipped, "Failures": failures}


# ------------------------------------------------------------------ contact flows
def list_contact_flows(event):
    kw = {"InstanceId": _instance(event)}
    if event.get("ContactFlowType"):
        kw["ContactFlowTypes"] = [event["ContactFlowType"]]
    flows = _pages("list_contact_flows", "ContactFlowSummaryList", **kw)
    return {"ContactFlows": [{"Id": f["Id"], "Arn": f["Arn"], "Name": f["Name"], "ContactFlowType": f.get("ContactFlowType"),
                              "ContactFlowState": f.get("ContactFlowState"), "ContactFlowStatus": f.get("ContactFlowStatus")}
                             for f in flows]}


def describe_contact_flow(event):
    inst, ident = _instance(event), (event.get("ContactFlowIdentifier") or "").strip()
    if not ident:
        raise UserError("ContactFlowIdentifier is required")
    if not (ident.startswith("arn:") or re.fullmatch(r"[0-9a-f-]{36}", ident)):
        match = [f for f in list_contact_flows(event)["ContactFlows"] if f["Name"].lower() == ident.lower()]
        if not match:
            raise UserError(f"no contact flow named {ident!r}")
        ident = match[0]["Id"]
    f = connect().describe_contact_flow(InstanceId=inst, ContactFlowId=ident.rsplit("/", 1)[-1])["ContactFlow"]
    return {"Id": f["Id"], "Arn": f["Arn"], "Name": f["Name"], "Type": f.get("Type"), "Content": json.loads(f["Content"])}


REF = re.compile(r"\$\.DataTables\.([A-Za-z0-9_\-]+)((?:\.[A-Za-z0-9_\-]+)+|\['[^']+'\])")
TABLE_ARN = re.compile(r"arn:aws[-a-z]*:connect:[a-z0-9-]+:\d{12}:instance/[0-9a-f-]+/data-table/[0-9a-f-]+")


def _walk(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, path + (i,))
    elif isinstance(node, str):
        yield path, node


def identify_table_requirements(event):
    """Static analysis of a flow: which data table attributes does it read?

    Connect's runtime namespace is $.DataTables.<QueryName>.<Attribute>, where QueryName is the name of a query in a
    Data Table block (not the table name). We therefore report per query, and attach the table ARN when the block that
    defines the query references one. Placeholder convention (spec): a flow written before the table exists can use
    $.DataTables.<Table>.<Key...>.<Attribute>; dotted middle segments become primary-key VALUES (Key1..KeyN columns).
    """
    content = event.get("ContactFlowContent")
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, dict) or "Actions" not in content:
        raise UserError("ContactFlowContent must be a contact flow definition (with Actions)")
    flow_name = event.get("ContactFlowName") or content.get("Metadata", {}).get("name") or "ContactFlow"
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for action in content["Actions"]:
        block_tables = sorted({m for _, s in _walk(action.get("Parameters", {})) for m in TABLE_ARN.findall(s)})
        for path, s in _walk(action):
            for m in REF.finditer(s):
                query, rest = m.group(1), m.group(2)
                parts = [rest[2:-2]] if rest.startswith("['") else rest.strip(".").split(".")
                g = groups.setdefault(query, {"Query": query, "TableArns": set(), "Attributes": OrderedDict(),
                                              "KeyDepth": 0, "KeyExamples": [], "References": []})
                g["TableArns"].update(block_tables)
                g["Attributes"].setdefault(parts[-1], None)
                if len(parts) > 1:
                    g["KeyDepth"] = max(g["KeyDepth"], len(parts) - 1)
                    g["KeyExamples"].append(parts[:-1])
                g["References"].append({"BlockId": action.get("Identifier"), "BlockType": action.get("Type"),
                                        "AttributePath": m.group(0)})
    tables = []
    for g in groups.values():
        attrs = [{"Name": f"Key{i + 1}", "ValueType": "TEXT", "Primary": True,
                  "Description": "examples: " + ", ".join(sorted({e[i] for e in g["KeyExamples"] if len(e) > i}))[:240]}
                 for i in range(g["KeyDepth"])]
        attrs += [{"Name": a, "ValueType": "TEXT", "Primary": False} for a in g["Attributes"]]
        tables.append({"TableName": g["Query"] if g["Query"] != "Query" else flow_name, "Query": g["Query"],
                       "ExistingTableArns": sorted(g["TableArns"]), "Attributes": attrs, "References": g["References"]})
    out = {"Tables": tables, "Status": f"{len(tables)} table(s) referenced" if tables else "no $.DataTables references found"}
    if len(tables) == 1:  # spec shape: pass straight to CreateDataTable
        out.update({"TableName": tables[0]["TableName"], "Attributes": tables[0]["Attributes"], "References": tables[0]["References"]})
    return out


OPS = {
    "ListDataTables": list_data_tables, "DescribeDataTable": describe_data_table, "GetDataTableRows": get_data_table_rows,
    "CreateDataTable": create_data_table, "UpdateDataTable": update_data_table, "ListContactFlows": list_contact_flows,
    "DescribeContactFlow": describe_contact_flow, "IdentifyTableRequirements": identify_table_requirements,
}


def lambda_handler(event, _context):
    op = event.get("operation") or event.get("Operation")
    if op not in OPS:
        return {"error": f"unknown operation {op!r}", "operations": sorted(OPS)}
    try:
        return OPS[op](event)
    except UserError as e:
        return {"error": str(e)}
    except ClientError as e:  # AWS error: surface code + message, no stack
        return {"error": e.response["Error"]["Code"], "message": e.response["Error"].get("Message", "")}
