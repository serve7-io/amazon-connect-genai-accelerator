import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("dtm_handler", Path(__file__).resolve().parents[1] / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)

INST = "11111111-2222-3333-4444-555555555555"
TID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeConnect:
    """In-memory stand-in for the Connect data-table and flow APIs (only what the handler calls)."""

    def __init__(self):
        self.tables = {TID: {"Id": TID, "Arn": f"arn:aws:connect:ca-central-1:123456789012:instance/{INST}/data-table/{TID}",
                             "Name": "IVR_Prompts_English", "Description": "", "TimeZone": "America/Toronto", "ValueLockLevel": "PRIMARY_VALUE"}}
        self.attrs = {TID: [{"Name": "Department", "ValueType": "TEXT", "Primary": True},
                            {"Name": "WelcomeMessage", "ValueType": "TEXT", "Primary": False},
                            {"Name": "MaxRetries", "ValueType": "NUMBER", "Primary": False}]}
        self.values = {TID: [{"AttributeName": "WelcomeMessage", "Value": "Welcome to Sales.",
                              "PrimaryValues": [{"AttributeName": "Department", "Value": "Sales"}], "LockVersion": {"Value": "1"}}]}
        self.calls = []

    def list_data_tables(self, InstanceId, **_):
        return {"DataTableSummaryList": [{"Id": t["Id"], "Arn": t["Arn"], "Name": t["Name"]} for t in self.tables.values()]}

    def describe_data_table(self, InstanceId, DataTableId):
        return {"DataTable": self.tables[DataTableId]}

    def list_data_table_attributes(self, InstanceId, DataTableId, **_):
        return {"Attributes": self.attrs[DataTableId]}

    def list_data_table_values(self, InstanceId, DataTableId, **_):
        return {"Values": self.values[DataTableId]}

    def create_data_table(self, **kw):
        self.calls.append(("create_data_table", kw))
        nid = "ffffffff-0000-0000-0000-000000000001"
        self.tables[nid] = {"Id": nid, "Arn": f"arn:x/data-table/{nid}", "Name": kw["Name"]}
        self.attrs[nid], self.values[nid] = [], []
        return {"Id": nid, "Arn": f"arn:x/data-table/{nid}"}

    def create_data_table_attribute(self, **kw):
        self.calls.append(("create_data_table_attribute", kw))
        self.attrs[kw["DataTableId"]].append(kw)
        return {"AttributeId": kw["Name"]}

    def batch_create_data_table_value(self, InstanceId, DataTableId, Values):
        self.calls.append(("create_values", Values))
        return {"Successful": Values, "Failed": []}

    def batch_update_data_table_value(self, InstanceId, DataTableId, Values):
        self.calls.append(("update_values", Values))
        return {"Successful": Values, "Failed": []}

    def list_contact_flows(self, InstanceId, **kw):
        return {"ContactFlowSummaryList": [{"Id": "f1", "Arn": "arn:f1", "Name": "Main", "ContactFlowType": "CONTACT_FLOW"}]}

    def describe_contact_flow(self, InstanceId, ContactFlowId):
        return {"ContactFlow": {"Id": "f1", "Arn": "arn:f1", "Name": "Main", "Type": "CONTACT_FLOW",
                                "Content": json.dumps({"Version": "2019-10-30", "Actions": []})}}


@pytest.fixture
def fake(monkeypatch):
    f = FakeConnect()
    monkeypatch.setattr(handler, "_connect", f)
    monkeypatch.setattr(handler, "INSTANCE_ID", INST)
    return f


def call(op, **kw):
    return handler.lambda_handler({"operation": op, **kw}, None)


def test_list_and_describe_by_name(fake):
    assert call("ListDataTables")["DataTables"][0]["Name"] == "IVR_Prompts_English"
    d = call("DescribeDataTable", DataTableIdentifier="ivr_prompts_english")
    assert d["DataTableId"] == TID and d["Attributes"][0] == {"Name": "Department", "ValueType": "TEXT", "Primary": True,
                                                              "Validation": {}, "Description": ""}


def test_rows_pivot(fake):
    r = call("GetDataTableRows", DataTableIdentifier=TID)
    assert r["Rows"] == [{"Department": "Sales", "WelcomeMessage": "Welcome to Sales."}] and r["RowCount"] == 1


def test_create_primary_attributes_first(fake):
    out = call("CreateDataTable", TableName="Emergency_Closures",
               Attributes=[{"Name": "Message", "ValueType": "TEXT"}, {"Name": "Site", "ValueType": "TEXT", "Primary": True}])
    assert out["AttributesCreated"] == ["Site", "Message"]
    assert fake.calls[0][1]["Status"] == "PUBLISHED" and fake.calls[0][1]["ValueLockLevel"] == "PRIMARY_VALUE"


def test_create_rejects_bad_type_and_duplicates(fake):
    assert "ValueType" in call("CreateDataTable", TableName="x", Attributes=[{"Name": "a", "ValueType": "DATE"}])["error"]
    assert "unique" in call("CreateDataTable", TableName="x", Attributes=[{"Name": "a"}, {"Name": "A"}])["error"]


def test_upsert_creates_updates_and_skips(fake):
    out = call("UpdateDataTable", DataTableIdentifier=TID, Operations=[
        {"PrimaryKeyValues": {"Department": "Sales"}, "AttributeValues": {"WelcomeMessage": "Thanks for calling Sales.", "MaxRetries": 3}},
        {"PrimaryKeyValues": {"Department": "Billing"}, "AttributeValues": {"WelcomeMessage": "Welcome to Billing."}},
    ])
    assert out == {"SuccessCount": 3, "Created": 2, "Updated": 1, "Unchanged": 0, "Failures": []}
    upd = [c for c in fake.calls if c[0] == "update_values"][0][1][0]
    assert upd["LockVersion"] == {"Value": "1"} and upd["Value"] == "Thanks for calling Sales."


def test_create_only_never_overwrites(fake):
    out = call("UpdateDataTable", DataTableIdentifier=TID, Mode="create_only", Operations=[
        {"PrimaryKeyValues": {"Department": "Sales"}, "AttributeValues": {"WelcomeMessage": "changed"}}])
    assert out["Updated"] == 0 and out["Unchanged"] == 1


def test_update_validates_keys(fake):
    out = call("UpdateDataTable", DataTableIdentifier=TID, Operations=[
        {"PrimaryKeyValues": {"Dept": "Sales"}, "AttributeValues": {"WelcomeMessage": "x"}},
        {"PrimaryKeyValues": {"Department": "Sales"}, "AttributeValues": {"Nope": "x"}}])
    assert len(out["Failures"]) == 2 and out["SuccessCount"] == 0


def test_other_instance_refused(fake):
    assert "own Connect instance" in call("ListDataTables", InstanceId="99999999-0000-0000-0000-000000000000")["error"]


def test_describe_flow_by_name(fake):
    assert call("DescribeContactFlow", ContactFlowIdentifier="main")["Content"]["Version"] == "2019-10-30"


def test_identify_requirements():
    flow = {"Version": "2019-10-30", "Metadata": {"name": "Sales IVR"}, "Actions": [
        {"Identifier": "dt", "Type": "EvaluateDataTable", "Parameters": {
            "DataTableId": f"arn:aws:connect:ca-central-1:123456789012:instance/{INST}/data-table/{TID}"}},
        {"Identifier": "p1", "Type": "MessageParticipant", "Parameters": {"Text": "$.DataTables.Prompts.WelcomeMessage"}},
        {"Identifier": "p2", "Type": "MessageParticipant", "Parameters": {"Text": "$.DataTables.Prompts['Hold Message']"}},
    ]}
    out = handler.lambda_handler({"operation": "IdentifyTableRequirements", "ContactFlowContent": flow}, None)
    assert out["TableName"] == "Prompts"
    assert [a["Name"] for a in out["Attributes"]] == ["WelcomeMessage", "Hold Message"]
    assert [r["BlockId"] for r in out["References"]] == ["p1", "p2"]


def test_identify_placeholder_keys_and_empty():
    flow = {"Actions": [{"Identifier": "a", "Type": "MessageParticipant",
                         "Parameters": {"Text": "$.DataTables.Sales.English.Welcome / $.DataTables.Sales.French.Welcome"}}]}
    out = handler.lambda_handler({"operation": "IdentifyTableRequirements", "ContactFlowContent": json.dumps(flow)}, None)
    assert out["Attributes"][0]["Name"] == "Key1" and out["Attributes"][0]["Primary"]
    assert "English" in out["Attributes"][0]["Description"] and out["Attributes"][1]["Name"] == "Welcome"
    empty = handler.lambda_handler({"operation": "IdentifyTableRequirements", "ContactFlowContent": {"Actions": []}}, None)
    assert empty["Tables"] == [] and "no $.DataTables" in empty["Status"]
