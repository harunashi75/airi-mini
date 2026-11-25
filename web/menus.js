'use strict';

(() => {
    const desktop = globalThis.airiSettings;
    const themes = ['system', 'light', 'dark'];
    const themeChoices = document.getElementById('themeChoices');
    const themeColor = document.querySelector('meta[name="theme-color"]');
    const settingsError = document.getElementById('settingsError');
    const systemTheme = matchMedia('(prefers-color-scheme: dark)');
    const preferenceKey = 'airi.theme';
    let theme = 'system';
    let changedByUser = false;

    function applyAppearance() {
        const dark = theme === 'dark' || (theme === 'system' && systemTheme.matches);
        document.documentElement.dataset.theme = dark ? 'dark' : 'light';
        if (themeColor) themeColor.content = dark ? '#211827' : '#f7ecf4';
        themeChoices.querySelectorAll('input').forEach(input => input.checked = input.value === theme);
    }

    function showError(error) {
        settingsError.textContent = error.message;
        settingsError.hidden = false;
    }

    async function readTheme() {
        try {
            const saved = desktop ? await desktop.readTheme() : localStorage.getItem(preferenceKey);
            if (!changedByUser && themes.includes(saved)) {
                theme = saved;
                applyAppearance();
            }
        } catch (error) {
            if (desktop) showError(error);
        }
    }

    systemTheme.addEventListener('change', applyAppearance);
    applyAppearance();
    readTheme();

    themeChoices.addEventListener('change', async event => {
        const choice = event.target.value;
        if (!themes.includes(choice)) return;
        const previous = theme;
        changedByUser = true;
        settingsError.hidden = true;
        theme = choice;
        applyAppearance();
        try {
            if (desktop) await desktop.saveTheme(choice);
            else localStorage.setItem(preferenceKey, choice);
        } catch (error) {
            if (desktop && theme === choice) {
                theme = previous;
                applyAppearance();
                showError(error);
            }
        }
    });

    if (desktop) {
        document.querySelectorAll('[data-desktop]').forEach(section => section.hidden = false);
        document.querySelectorAll('[data-settings-action]').forEach(button => {
            button.addEventListener('click', async () => {
                try {
                    button.closest('[popover]').hidePopover();
                    await desktop[button.dataset.settingsAction]();
                } catch (error) {
                    showError(error);
                    document.getElementById('settingsMenu').showPopover();
                }
            });
        });
    }
})();
