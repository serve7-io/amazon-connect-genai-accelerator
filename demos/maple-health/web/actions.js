  // Simulated email inbox: polls GET <api>/inbox (public, demo @example.com addresses only, last 30 minutes) and shows the
  // verification emails. Plain text only (textContent), never markup. The inbox URL comes from demo.json web.page.inbox_url.
  const inboxUrl = (CFG.page && typeof CFG.page.inbox_url === "string" && /^https:\/\//.test(CFG.page.inbox_url)) ? CFG.page.inbox_url : "";
  const list = document.getElementById("inbox-list");
  const hint = document.getElementById("inbox-hint");
  const seen = new Set();
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) { e.className = cls; } if (text) { e.textContent = text; } return e; };
  const render = (messages) => {
    list.replaceChildren();
    if (!messages.length) { list.appendChild(el("li", "meta", "No emails yet. Ask the assistant to verify you.")); return; }
    messages.forEach((m) => {
      const key = m.to + m.sent_at;
      const li = el("li", seen.has(key) ? "" : "new");
      seen.add(key);
      const when = new Date(m.sent_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      li.appendChild(el("div", "meta", "To " + m.to + " · " + when));
      li.appendChild(el("div", "subj", m.subject));
      const code = (String(m.body).match(/\b\d{6}\b/) || [""])[0];
      if (code) { li.appendChild(el("div", "code", code)); }
      li.appendChild(el("div", "meta", m.body));
      list.appendChild(li);
    });
  };
  const poll = async () => {
    try {
      const r = await fetch(inboxUrl, { cache: "no-store" });
      if (r.ok) { render(((await r.json()).messages) || []); }
    } catch (e) { console.warn("inbox", e); }
  };
  if (inboxUrl) {
    hint.textContent = "Verification emails for the demo patients appear here within a few seconds (a real deployment sends them with Amazon SES).";
    await poll();
    setInterval(poll, 4000);
  } else {
    hint.textContent = "The demo inbox is not connected yet.";
  }
