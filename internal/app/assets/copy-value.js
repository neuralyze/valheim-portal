// Click-to-copy for the join address and the signed-in Steam ID. Both values are
// rendered as selectable text first and the buttons ship hidden, so a browser
// without clipboard access shows no control that cannot work.
(() => {
  if (!navigator.clipboard) {
    return;
  }
  for (const button of document.querySelectorAll("button[data-copy]")) {
    button.hidden = false;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
      } catch {
        return;
      }
      const label = button.textContent;
      button.textContent = "Copied";
      button.classList.add("copied");
      setTimeout(() => {
        button.textContent = label;
        button.classList.remove("copied");
      }, 1600);
    });
  }
})();
