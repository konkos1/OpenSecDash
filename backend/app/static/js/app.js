
document.addEventListener("DOMContentLoaded", () => {

    document.querySelectorAll("[data-confirm]")
        .forEach(element => {

            element.addEventListener("submit", event => {

                const text = element.dataset.confirm;

                if (!confirm(text)) {
                    event.preventDefault();
                }

            });

        });

});
