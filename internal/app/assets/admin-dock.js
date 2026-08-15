// The agent dock on the administration home.
//
// Why a dock at all: the operator's work is on this page - servers, players, releases - and the
// agent is a thing you ask about that work. Making them talk to it on a different page meant
// leaving the surface they were operating, and coming back to find out whether anything answered.
//
// It is deliberately not the full agent page. It shows the last few turns, takes a message, and
// says when a reply is owed or a decision is waiting. Approving a verb happens on /admin/agent,
// where the arguments and the release context are shown in full - a decision made from a summary
// in the corner of the screen is exactly the habit that page exists to prevent.
(function () {
  var dock = document.querySelector("[data-agent-dock]");
  if (!dock) {
    return;
  }
  var log = dock.querySelector("[data-dock-log]");
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
      note.textContent = "A request needs your decision on the agent page.";
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
