// Language switching for the standalone onboarding page. The page must work
// without JavaScript, so this only replaces the visible submit button of the
// language form with an immediate switch - the same GET, one field, never a
// username or a password in the URL.
document.addEventListener("DOMContentLoaded", () => {
    const languageForm = document.querySelector("form[data-language-form]");
    const languageSelect = document.querySelector("select[data-language-select]");
    const mainForm = document.querySelector("form[data-unsaved-warning]");

    if (!languageForm || !languageSelect) {
        return;
    }

    const submitButton = languageForm.querySelector("button[type=submit]");
    if (submitButton) {
        submitButton.hidden = true;
    }

    let currentLanguage = languageSelect.value;

    const hasInput = () => Array.from(mainForm ? mainForm.elements : [])
        .some(element => element.type !== "hidden" && element.value);

    languageSelect.addEventListener("change", () => {
        const message = mainForm ? mainForm.dataset.unsavedWarning : "";
        if (hasInput() && message && !confirm(message)) {
            languageSelect.value = currentLanguage;
            return;
        }
        currentLanguage = languageSelect.value;
        languageForm.submit();
    });
});
