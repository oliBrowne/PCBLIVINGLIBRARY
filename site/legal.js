/* theme button for the legal pages: same stored choice as the landing page (site.js) */
const root = document.documentElement;
try { const t = localStorage.getItem("ecadlib-theme"); if (t === "light" || t === "dark") root.dataset.theme = t; } catch {}
const dark = () => root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
const themeBtn = document.getElementById("themebtn");
const labelTheme = () => { themeBtn.textContent = dark() ? "light" : "dark"; };
labelTheme();
themeBtn.addEventListener("click", () => {
  root.dataset.theme = dark() ? "light" : "dark";
  try { localStorage.setItem("ecadlib-theme", root.dataset.theme); } catch {}
  labelTheme();
});
