// The agent dock on the administration home.
//
// Why a dock at all: the operator's work is on this page - servers, players, releases - and the
// agent is a thing you ask about that work. Making them talk to it on a different page meant
// leaving the surface they were operating, and coming back to find out whether anything answered.
//
// It shows the last few turns, takes a message, and decides waiting requests - but never from a
// summary. Every argument the call carries is rendered here, from the same builder the full page
// uses, because the rule worth keeping is not "decide elsewhere", it is "never approve something you
// cannot see". A publish also shows what that world already serves and how many releases went out
// today, since those two numbers are what make a publish approval informed.
(function () {
  var dock = document.querySelector("[data-agent-dock]");
  if (!dock) {
    return;
  }
  var log = dock.querySelector("[data-dock-log]");
  var awaiting = dock.querySelector("[data-dock-awaiting]");
  var badge = dock.querySelector("[data-dock-badge]");
  var box = dock.querySelector("textarea[name=body]");
  var form = dock.querySelector("form");
  var note = dock.querySelector("[data-dock-note]");
  var known = "";
  var timer = null;

  // Open state survives a reload, because the operator reopening it after every action would be
  // the same annoyance in a different place.
  var openKey = "valheim-agent-dock-open";
  try {
    if (window.localStorage.getItem(openKey) === "true") {
      dock.open = true;
    }
  } catch (error) {
    // Private mode or a blocked store: the dock still works, it just does not remember.
  }
  dock.addEventListener("toggle", function () {
    try {
      window.localStorage.setItem(openKey, dock.open ? "true" : "false");
    } catch (error) {
      /* ignore */
    }
    schedule();
    if (dock.open) {
      refresh();
    }
  });

  function turn(role, body) {
    var article = document.createElement("article");
    article.className = "dock-turn dock-turn-" + role;
    var who = document.createElement("h4");
    who.textContent = role;
    var text = document.createElement("pre");
    // textContent, never innerHTML: these turns carry host output and an operator's own words.
    text.textContent = body;
    article.appendChild(who);
    article.appendChild(text);
    return article;
  }

  function decide(id, decision) {
    var payload = new URLSearchParams();
    payload.set("csrf", form.querySelector("input[name=csrf]").value);
    payload.set("id", id);
    payload.set("decision", decision);
    note.textContent = decision === "approve" ? "running..." : "recording the refusal...";
    return fetch("/admin/agent/decide", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: payload.toString(),
      redirect: "manual"
    })
      .then(function () {
        known = "";
        note.textContent = "";
        return refresh();
      })
      .catch(function (error) {
        note.textContent = "Could not " + decision + ": " + error.message;
      });
  }

  function requestCard(call) {
    var card = document.createElement("section");
    card.className = "dock-request";

    var title = document.createElement("h4");
    title.textContent = call.verb + " · " + call.class;
    card.appendChild(title);

    // Every argument, one per row. This is the whole justification for deciding from the dock.
    var table = document.createElement("dl");
    table.className = "dock-args";
    call.arguments.forEach(function (argument) {
      var name = document.createElement("dt");
      name.textContent = argument.name;
      var value = document.createElement("dd");
      value.textContent = argument.value;
      table.appendChild(name);
      table.appendChild(value);
    });
    if (call.arguments.length === 0) {
      var none = document.createElement("dd");
      none.textContent = "no arguments";
      table.appendChild(none);
    }
    card.appendChild(table);

    if (call.live) {
      var context = document.createElement("p");
      context.className = "dock-context";
      context.textContent = call.live.length > 0
        ? "Now serving: " + call.live.join(", ") + ". " + call.published_today + " release(s) published today."
        : call.published_today + " release(s) published today; this world serves nothing yet.";
      card.appendChild(context);
    }

    var controls = document.createElement("div");
    controls.className = "dock-actions";
    [["approve", "Approve"], ["deny", "Deny"]].forEach(function (pair) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = pair[1];
      if (pair[0] === "deny") {
        button.className = "danger";
      }
      button.addEventListener("click", function () {
        controls.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
        decide(call.id, pair[0]);
      });
      controls.appendChild(button);
    });
    card.appendChild(controls);
    return card;
  }

  function render(state) {
    if (state.state === known) {
      return;
    }
    known = state.state;
    log.textContent = "";
    if (!state.bridge_enabled) {
      note.textContent = "The agent bridge is disabled, so nothing can answer.";
      return;
    }
    if (state.turns.length === 0) {
      log.appendChild(turn("system", "Nothing yet. Ask for something."));
    }
    state.turns.forEach(function (item) {
      log.appendChild(turn(item.role, item.body));
    });
    // The newest turn is the one being read.
    log.scrollTop = log.scrollHeight;

    awaiting.textContent = "";
    (state.awaiting || []).forEach(function (call) {
      awaiting.appendChild(requestCard(call));
    });

    var parts = [];
    if (state.waiting) {
      parts.push("working " + state.waited_seconds + "s");
    }
    if (state.pending > 0) {
      parts.push(state.pending + " awaiting you");
    }
    badge.textContent = parts.length > 0 ? parts.join(", ") : "";
    badge.hidden = parts.length === 0;
    if (state.waiting && state.waited_seconds > 90) {
      note.textContent = "No reply in " + state.waited_seconds + "s - the runner may not be running.";
    } else if (state.pending > 0) {
      note.textContent = "";
    } else {
      note.textContent = "";
    }
  }

  function refresh() {
    return fetch("/admin/agent/tail.json", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("status " + response.status);
        }
        return response.json();
      })
      .then(render)
      .catch(function (error) {
        note.textContent = "Cannot reach the portal: " + error.message;
      });
  }

  // Two seconds while a reply is owed, ten otherwise, and nothing at all while closed: a dock
  // nobody is looking at should not poll.
  function schedule() {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
    if (!dock.open) {
      return;
    }
    timer = window.setInterval(refresh, 2000);
  }

  function send() {
    var body = box.value.trim();
    if (body === "") {
      return;
    }
    var payload = new URLSearchParams();
    payload.set("csrf", form.querySelector("input[name=csrf]").value);
    payload.set("body", body);
    note.textContent = "sending...";
    fetch("/admin/agent/message", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: payload.toString(),
      // The handler answers with a redirect to the full page; the dock stays where it is.
      redirect: "manual"
    })
      .then(function () {
        box.value = "";
        note.textContent = "";
        known = "";
        return refresh();
      })
      .catch(function (error) {
        note.textContent = "Could not send: " + error.message;
      });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    send();
  });
  box.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send();
    }
  });

  if (dock.open) {
    refresh();
    schedule();
  } else {
    // One read while closed, so the badge can say something is waiting before it is opened.
    refresh();
  }
})();
