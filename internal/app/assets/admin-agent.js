// Keeps /admin/agent current without the operator reloading it, and without throwing away what
// they are typing.
//
// The page is server-rendered, so "live" here means: ask a small JSON endpoint whether anything
// changed, and reload only when it did. Two rules matter more than freshness:
//
//   * never reload while the operator has text in the message box, or mid-sentence their words
//     vanish and the page looks broken;
//   * never touch the Approve and Deny buttons. Approval stays a deliberate click on a page the
//     operator is looking at - nothing here submits anything.
(function () {
  "use strict";

  var status = document.querySelector("[data-agent-status]");
  if (!status) {
    return;
  }
  var known = status.getAttribute("data-agent-status");
  var indicator = document.querySelector("[data-agent-indicator]");
  var box = document.querySelector("textarea[name='body']");
  var failures = 0;

  function typing() {
    return box && box.value.trim().length > 0;
  }

  function show(text) {
    if (indicator) {
      indicator.textContent = text;
    }
  }

  function poll() {
    fetch("/admin/agent/status.json", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("status " + response.status);
        }
        return response.json();
      })
      .then(function (state) {
        failures = 0;
        if (state.state === known) {
          return;
        }
        if (typing()) {
          // Say so rather than silently going stale: the operator can see there is something new
          // and reload when their message is sent.
          show("new activity - reload when you are done typing");
          return;
        }
        window.location.reload();
      })
      .catch(function (error) {
        failures += 1;
        if (failures === 3) {
          show("cannot reach the portal: " + error.message);
        }
      });
  }


  // Ctrl+Enter (or Cmd+Enter) sends. A message long enough to need a textarea is long enough that
  // reaching for the mouse breaks the thought, and Enter alone cannot send: these messages are
  // multi-line by nature.
  var box = document.querySelector("textarea[name=body]");
  if (box && box.form) {
    box.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) {
        return;
      }
      event.preventDefault();
      if (box.value.trim() === "") {
        return;
      }
      if (box.form.requestSubmit) {
        box.form.requestSubmit();
      } else {
        box.form.submit();
      }
    });
  }

  // The newest turn and the working indicator sit together at the end of the conversation, so put
  // the end of the conversation on screen. Without this the operator lands at the top and has to
  // scroll to find both the message they just sent and the answer to it.
  var turns = document.querySelectorAll(".agent-turn");
  if (turns.length > 0 && !window.location.hash) {
    turns[turns.length - 1].scrollIntoView({ block: "end" });
  }

  // The elapsed counter ticks locally, once a second, while the agent owes a turn. It is what makes
  // "still working" believable: a static spinner and a static number look identical to a dead page,
  // and the operator's real question is whether anything is still happening.
  var elapsed = document.querySelector("[data-agent-elapsed]");
  var working = document.querySelector("[data-agent-working]");
  if (elapsed && working) {
    var started = Date.now() - Number(working.getAttribute("data-agent-working") || 0) * 1000;
    window.setInterval(function () {
      elapsed.textContent = String(Math.round((Date.now() - started) / 1000));
    }, 1000);
  }

  // Two seconds while the agent owes a turn, five while an approval waits, thirty when idle. A verb
  // that takes minutes should not cost an operator a reload every five seconds for an hour, but a
  // reply they are watching for should not sit unseen for thirty either.
  var interval = 30000;
  if (status.getAttribute("data-agent-waiting") === "true") {
    interval = 2000;
  } else if (status.getAttribute("data-agent-busy") === "true") {
    interval = 5000;
  }
  window.setInterval(poll, interval);
})();
