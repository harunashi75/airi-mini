'use strict';

const choose = document.getElementById('choose');
const retry = document.getElementById('retry');
let loading = true;

window.airiSetup.subscribe(state => {
    loading = state.loading;
    document.getElementById('title').textContent = state.title;
    document.getElementById('message').textContent = state.message;
    document.getElementById('progress').hidden = loading === false;
    document.getElementById('actions').hidden = loading;
});

for (const [button, action] of [[choose, window.airiSetup.chooseModel], [retry, window.airiSetup.retry]]) {
    button.addEventListener('click', async () => {
        choose.disabled = retry.disabled = true;
        try {
            await action();
        } catch (error) {
            document.getElementById('message').textContent = error.message;
        } finally {
            choose.disabled = retry.disabled = false;
        }
    });
}
