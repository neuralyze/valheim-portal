(() => {
  'use strict';

  const catalog = document.getElementById('admin-profile-catalog');
  if (!catalog || !('content' in catalog)) return;

  const choices = catalog.content.querySelectorAll('option[data-world]');
  document.querySelectorAll('form[data-profile-autofill]').forEach((form) => {
    const world = form.querySelector('[data-profile-world]');
    const profile = form.querySelector('[data-profile-slug]');
    const list = form.querySelector('datalist');
    if (!world || !profile || !list) return;

    const updateChoices = () => {
      const matching = document.createDocumentFragment();
      choices.forEach((choice) => {
        if (choice.dataset.world === world.value) {
          matching.appendChild(choice.cloneNode(true));
        }
      });
      list.replaceChildren(matching);
    };

    world.addEventListener('change', updateChoices);
    window.addEventListener('pageshow', updateChoices);
    updateChoices();
  });
})();
