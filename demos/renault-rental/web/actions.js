  // Live Sync: the page actions the assistant can call. Names, descriptions and schemas are advertised to the agent by
  // touchpoint.sendContext; the same names are listed in the "Page actions" section of the renaultlivesync flow prompt.
  // The agent passes values it got from the rental API; handlers only update the page (plain text only, never markup)
  // and never call the API themselves, so no key is needed in the browser.
  const MODEL_IDS = ["twingo", "clio", "captur", "megane", "meganeetech", "arkana", "austral", "scenic", "kangoo", "trafic"];
  const byId = (id) => document.getElementById(id);
  const text = (id, v) => { const el = byId(id); if (el) { el.textContent = (v === undefined || v === null || v === "") ? "-" : String(v); } };
  const eur = (v) => (v === undefined || v === null || v === "" || isNaN(Number(v))) ? "-" : (Number(v).toFixed(2).replace(/\.00$/, "") + " EUR");
  const isDate = (v) => /^\d{4}-\d{2}-\d{2}$/.test(String(v || ""));
  const setField = (id, v) => {
    const el = byId(id);
    if (!el || v === undefined || v === null || v === "") { return; }
    el.value = String(v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const toModelId = (v) => {
    const s = String(v || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    return MODEL_IDS.find((id) => id === s) || MODEL_IDS.find((id) => s && (id.includes(s) || s.includes(id))) || "";
  };
  const highlight = (ids) => {
    document.querySelectorAll(".car").forEach((c) => c.classList.toggle("hl", ids.includes(c.dataset.modelId)));
    const first = document.querySelector(".car.hl");
    if (first) { first.scrollIntoView({ behavior: "smooth", block: "center" }); }
  };
  const reveal = (id) => { const el = byId(id); if (el) { el.hidden = false; el.scrollIntoView({ behavior: "smooth", block: "nearest" }); } };
  const dates = (a, b) => [a, b].filter(Boolean).join(" to ");
  // the confirmation panel is reused for a cancellation: heading, colour and pickup note change, the number stays
  const setCancelled = (on) => {
    const card = byId("confirm-card");
    card.classList.toggle("cancelled", on);
    card.querySelector("h2").textContent = on ? "Booking cancelled" : "Booking confirmed";
    const note = card.querySelector(".hint");
    if (note) { note.hidden = on; }
  };

  try {
    await touchpoint.sendContext({
      scopes: ["car_rental"],
      actions: [
        {
          action: "highlight_cars",
          description: "Highlight car cards on the page for the car models you are talking about, so the customer sees them. Pass an empty list to clear.",
          schema: { type: "object", properties: { model_ids: { type: "array", items: { type: "string", enum: MODEL_IDS }, description: "Model ids to highlight" } }, required: ["model_ids"] },
          handler: ({ model_ids }) => highlight((Array.isArray(model_ids) ? model_ids : []).map(toModelId).filter(Boolean))
        },
        {
          action: "set_booking_details",
          description: "Fill the booking form on the page with the details you know so far. Send only what you know; call again when something changes.",
          schema: {
            type: "object",
            properties: {
              model: { type: "string", enum: MODEL_IDS, description: "Car model id" },
              start_date: { type: "string", description: "Pickup date, YYYY-MM-DD" },
              end_date: { type: "string", description: "Return date, YYYY-MM-DD" },
              licence: { type: "string", enum: ["B", "A2", "A1", "A", "AM"], description: "Driving licence category" },
              customer_name: { type: "string", description: "Customer's full name" },
              phone: { type: "string", description: "Customer's phone number" }
            }
          },
          handler: (d) => {
            d = d || {};
            const id = toModelId(d.model);
            if (id) { setField("model", id); highlight([id]); }
            if (isDate(d.start_date)) { setField("start_date", d.start_date); }
            if (isDate(d.end_date)) { setField("end_date", d.end_date); }
            if (["B", "A2", "A1", "A", "AM"].includes(String(d.licence || "").toUpperCase())) { setField("licence", String(d.licence).toUpperCase()); }
            setField("customer_name", d.customer_name);
            setField("phone", d.phone);
            byId("booking-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
          }
        },
        {
          action: "show_quote",
          description: "Show the price panel after you got a quote from the rental API. Pass the numbers exactly as the API returned them.",
          schema: {
            type: "object",
            properties: {
              model: { type: "string", description: "Car model name" },
              start_date: { type: "string", description: "Pickup date YYYY-MM-DD" },
              return_date: { type: "string", description: "Return date YYYY-MM-DD" },
              days: { type: "number" }, subtotal_eur: { type: "number" }, discount_percent: { type: "number" }, discount_eur: { type: "number" },
              total_eur: { type: "number" }, deposit_eur: { type: "number" }, km_included: { type: "number" }
            },
            required: ["model", "total_eur"]
          },
          handler: (q) => {
            q = q || {};
            text("q-model", q.model); text("q-dates", dates(q.start_date, q.return_date) || "-"); text("q-days", q.days);
            text("q-subtotal", eur(q.subtotal_eur));
            text("q-discount", q.discount_percent ? (q.discount_percent + "% off, -" + eur(q.discount_eur)) : "none");
            text("q-total", eur(q.total_eur)); text("q-deposit", eur(q.deposit_eur));
            text("q-km", q.km_included ? (q.km_included + " km") : "-");
            reveal("quote-card");
          }
        },
        {
          action: "show_confirmation",
          description: "Show the booking confirmation panel after the rental API created the reservation (created true). Pass the details exactly as returned.",
          schema: {
            type: "object",
            properties: {
              confirmation: { type: "string", description: "Confirmation number, e.g. REN-12345" },
              model: { type: "string" }, start_date: { type: "string" }, return_date: { type: "string" },
              total_eur: { type: "number" }, deposit_eur: { type: "number" }
            },
            required: ["confirmation"]
          },
          handler: (c) => {
            c = c || {};
            text("c-number", c.confirmation); text("c-model", c.model); text("c-dates", dates(c.start_date, c.return_date) || "-");
            text("c-total", eur(c.total_eur)); text("c-deposit", eur(c.deposit_eur));
            setCancelled(false);
            reveal("confirm-card");
          }
        },
        {
          action: "show_cancellation",
          description: "Show that a booking was cancelled, after the rental API cancelled it (cancelled true). The confirmation panel changes to cancelled.",
          schema: { type: "object", properties: { confirmation: { type: "string", description: "The cancelled confirmation number, e.g. REN-12345" } }, required: ["confirmation"] },
          handler: (c) => {
            c = c || {};
            text("c-number", c.confirmation);
            setCancelled(true);
            reveal("confirm-card");
          }
        }
      ]
    });
  } catch (e) {
    console.error("Live Sync actions were not registered", e);
  }
