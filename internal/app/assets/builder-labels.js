// Hands the operator's builder names to the map canvas.
//
// The names cannot be inlined into the page as a script literal: the admin CSP forbids inline script,
// which is deliberate. So they travel on this tag's data attribute and the canvas reads them from
// there, which keeps the policy intact and needs no second request.
(function () {
  var tag = document.currentScript;
  if (!tag) {
    return;
  }
  try {
    window.__builderLabels = JSON.parse(tag.getAttribute("data-labels") || "{}");
  } catch (error) {
    window.__builderLabels = {};
  }
})();
