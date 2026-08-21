// Behaviour for the world settings configuration manager. Three jobs, all of them refinements on a
// page that already works without this file: the filter box, the slider/number pairing, and the
// press-a-key chooser. Nothing here is required to read or save a setting - a browser with scripts
// off still gets every widget and every form, which is why the number field rather than the slider
// carries the submitted value.
//
// It is an external asset rather than an inline <script> because the portal's
// Content-Security-Policy has no script-src and so falls back to default-src 'self'. An inline
// script would be blocked outright and the page would look inert with no error the operator could
// see.
(function () {
  'use strict';

  // Filter the drawn rows as you type. This filters what is ON the page; the search box beside it is
  // a server round trip across the whole corpus, because one world declares far more settings than
  // any single view draws and a client filter cannot see the ones that were never sent.
  var filter = document.querySelector('[data-config-filter]');
  var settings = Array.prototype.slice.call(document.querySelectorAll('.config-setting[data-search]'));
  if (filter && settings.length) {
    var sections = Array.prototype.slice.call(document.querySelectorAll('.config-section'));
    var apply = function () {
      var needle = filter.value.trim().toLowerCase();
      settings.forEach(function (row) {
        row.hidden = needle !== '' && row.getAttribute('data-search').indexOf(needle) === -1;
      });
      // A section heading with nothing left under it is noise, so it goes too.
      sections.forEach(function (section) {
        var rows = section.querySelectorAll('.config-setting[data-search]');
        var shown = 0;
        for (var i = 0; i < rows.length; i++) {
          if (!rows[i].hidden) {
            shown++;
          }
        }
        section.hidden = rows.length > 0 && shown === 0;
      });
    };
    // input covers typing. search and change cover the ways a type=search field can be emptied
    // WITHOUT an input event - the native clear affordance and Escape - which would otherwise leave
    // the box looking empty while rows stayed hidden. Observed exactly that during the smoke test.
    ['input', 'search', 'change'].forEach(function (event) {
      filter.addEventListener(event, apply);
    });
    apply();
  }

  // Pair each slider with the number field that actually submits. The slider is deliberately not a
  // named input: with two inputs of the same name the stale one could win, and a browser with
  // scripts off would then post whatever the slider happened to render at rather than what was
  // typed.
  Array.prototype.forEach.call(document.querySelectorAll('input[type=range][data-slider-for]'), function (slider) {
    var field = document.getElementById(slider.getAttribute('data-slider-for'));
    if (!field) {
      return;
    }
    slider.removeAttribute('tabindex');
    slider.addEventListener('input', function () {
      field.value = slider.value;
    });
    field.addEventListener('input', function () {
      // Only mirror a value the slider can represent; a precise value outside the declared bounds
      // stays in the number field rather than being clamped behind the operator's back.
      var parsed = parseFloat(field.value);
      if (!isNaN(parsed)) {
        slider.value = parsed;
      }
    });
  });

  // BepInEx writes a KeyboardShortcut as Unity KeyCode names joined by " + ", the modifiers first:
  // "LeftShift + PageUp" is a real value in these files. The names are Unity's own, not the
  // browser's, so each one is mapped rather than guessed from event.key.
  //
  // Escape and Tab are deliberately absent from this map even though Unity has names for both.
  // Swallowing them would trap a keyboard user inside the capture with no way out: Escape is how
  // anyone expects to abandon a modal interaction and Tab is how they leave a control. Both cancel
  // the capture instead, alongside the visible Cancel button, so the affordance never becomes a
  // keyboard trap. A player who genuinely wants Escape or Tab bound types the name in the field,
  // which is the same field the datalist completes.
  var KEYS = {
    Enter: 'Return', Backspace: 'Backspace', Delete: 'Delete',
    ' ': 'Space', ArrowUp: 'UpArrow', ArrowDown: 'DownArrow', ArrowLeft: 'LeftArrow',
    ArrowRight: 'RightArrow', Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown',
    Insert: 'Insert', CapsLock: 'CapsLock', '-': 'Minus', '=': 'Equals', '[': 'LeftBracket',
    ']': 'RightBracket', ';': 'Semicolon', "'": 'Quote', ',': 'Comma', '.': 'Period',
    '/': 'Slash', '\\': 'Backslash', '`': 'BackQuote'
  };

  var unityName = function (event) {
    if (Object.prototype.hasOwnProperty.call(KEYS, event.key)) {
      return KEYS[event.key];
    }
    // event.code is layout-independent, which is what a keybind wants.
    var code = event.code || '';
    if (/^Key[A-Z]$/.test(code)) {
      return code.slice(3);
    }
    if (/^Digit[0-9]$/.test(code)) {
      return 'Alpha' + code.slice(5);
    }
    if (/^Numpad[0-9]$/.test(code)) {
      return 'Keypad' + code.slice(6);
    }
    if (/^F[0-9]{1,2}$/.test(code)) {
      return code;
    }
    return '';
  };

  Array.prototype.forEach.call(document.querySelectorAll('[data-key-capture]'), function (button) {
    var field = document.getElementById(button.getAttribute('data-key-capture'));
    if (!field) {
      return;
    }
    var original = button.textContent;
    var cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'secondary';
    cancel.textContent = 'Cancel';
    cancel.hidden = true;
    button.parentNode.insertBefore(cancel, button.nextSibling);

    var stop = function () {
      window.removeEventListener('keydown', capture, true);
      button.textContent = original;
      button.removeAttribute('data-capturing');
      cancel.hidden = true;
    };
    var capture = function (event) {
      // A modifier on its own is not a binding, so keep listening until a real key arrives.
      if (['Shift', 'Control', 'Alt', 'Meta'].indexOf(event.key) !== -1) {
        return;
      }
      // The two escape hatches, left to the browser: Escape abandons, Tab abandons and moves on.
      if (event.key === 'Escape' || event.key === 'Tab') {
        stop();
        return;
      }
      var name = unityName(event);
      if (name === '') {
        return;
      }
      event.preventDefault();
      var parts = [];
      if (event.ctrlKey) { parts.push('LeftControl'); }
      if (event.altKey) { parts.push('LeftAlt'); }
      if (event.shiftKey) { parts.push('LeftShift'); }
      parts.push(name);
      field.value = parts.join(' + ');
      stop();
    };
    var start = function () {
      button.textContent = 'Listening for a key';
      button.setAttribute('data-capturing', '1');
      cancel.hidden = false;
      window.addEventListener('keydown', capture, true);
    };
    button.addEventListener('click', function () {
      if (button.hasAttribute('data-capturing')) {
        stop();
      } else {
        start();
      }
    });
    cancel.addEventListener('click', stop);
  });
}());
