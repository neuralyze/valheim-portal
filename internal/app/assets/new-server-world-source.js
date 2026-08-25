// The world-source switch on /admin/servers/new.
//
// The gating is structural, not advisory: the inputs belonging to unselected sources are
// `disabled`, and a disabled control is not submitted at all. So a seed typed into the box
// and then abandoned for "random" never reaches the server, rather than reaching it and
// being dropped in the handler where nothing records that it happened. The server refuses
// the mismatch as well - this file only makes the refusal hard to trigger by accident, and
// the page is fully usable with scripting off because every field starts disabled and the
// radio the operator picks is what enables its own.
(function () {
    "use strict";
    var fieldset = document.getElementById("world-source");
    if (!fieldset) {
        return;
    }
    var radios = fieldset.querySelectorAll('input[name="world_mode"]');
    var groups = fieldset.querySelectorAll("[data-world-mode]");

    function apply() {
        var selected = "";
        for (var i = 0; i < radios.length; i++) {
            if (radios[i].checked) {
                selected = radios[i].value;
            }
        }
        for (var g = 0; g < groups.length; g++) {
            var group = groups[g];
            var active = group.getAttribute("data-world-mode") === selected;
            var fields = group.querySelectorAll("input:not([type=radio]),select");
            for (var f = 0; f < fields.length; f++) {
                fields[f].disabled = !active;
                // Clearing on deselect matters for the file input in particular: a
                // multi-hundred-megabyte archive left attached to a hidden field would be
                // uploaded on every subsequent submit of this form.
                if (!active) {
                    fields[f].value = "";
                }
            }
        }
    }

    for (var i = 0; i < radios.length; i++) {
        radios[i].addEventListener("change", apply);
    }
    apply();
})();
