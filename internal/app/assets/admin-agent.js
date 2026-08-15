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

  // Five seconds while something is pending or running, thirty when the page is idle. A verb that
  // takes minutes should not cost an operator a reload every five seconds for an hour.
  var interval = status.getAttribute("data-agent-busy") === "true" ? 5000 : 30000;
  window.setInterval(poll, interval);
})();
