// Бан аккаунта (владелец 30.10.2026, server/backend.py: ban_user): сервер отвечает access=banned — окно «Доступ
// закрыт» без Boosty и кнопки входа (подписка и вход заблокированному не помогут). Остальные окна — как были.
"use strict";
const { read, assert, run } = require("./_util");

function fakeDocument() {
  const make = (tag) => ({
    tag, children: [], hidden: false, textContent: "", className: "",
    setAttribute() {}, append(...items) { this.children.push(...items); }, addEventListener() {},
    classList: { toggle() {} },
  });
  return { createElement: make, body: make("body"), addEventListener() {} };
}

module.exports = async function () {
  for (const file of ["app/web/frontend/js/components/access_gate.js", "android/app/src/main/assets/js/access_gate.js"]) {
    const ctx = run(read(file), { window: {}, document: fakeDocument(), Bridge: { call() {} } });
    const gate = ctx.window.accessGate;
    const view = () => {
      const [root] = ctx.document.body.children;
      const [title, text, why, actions] = root.children[0].children;
      const [boosty, login] = actions.children;
      return { closed: !root.hidden, title: title.textContent, text: text.textContent, why: !why.hidden,
               boosty: !boosty.hidden, login: login.hidden ? null : login.textContent };
    };

    gate.apply({ allowed: false, access: "banned", boosty: "" });
    let v = view();
    assert(v.closed && v.title === "Доступ закрыт", `${file}: ${JSON.stringify(v)}`);
    assert(v.text.includes("закрыт администратором") && !v.why && !v.boosty && v.login === null, `${file}: ${JSON.stringify(v)}`);

    gate.apply({ allowed: false, access: "subscription_required", boosty: "" });
    v = view();
    assert(v.title === "Нужна подписка" && v.why && v.boosty && v.login === "Войти", `${file}: ${JSON.stringify(v)}`);

    gate.apply({ allowed: false, access: "login_required", boosty: "" });
    v = view();
    assert(v.title === "Войдите в аккаунт" && !v.boosty && v.login === "Войти или зарегистрироваться", `${file}: ${JSON.stringify(v)}`);
  }
};
