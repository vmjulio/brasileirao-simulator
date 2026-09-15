/* ---------- theme ---------- */

document.getElementById("theme").addEventListener("click", function(){
  var root = document.documentElement;
  var dark = root.getAttribute("data-theme") === "dark";
  root.setAttribute("data-theme", dark ? "light" : "dark");
  try { localStorage.setItem("theme", dark ? "light" : "dark"); } catch(e){}
  labelThemeButton();
  // Pages that draw with theme colours redraw on this event.
  document.dispatchEvent(new Event("themechange"));
});
try { var saved = localStorage.getItem("theme"); if (saved) document.documentElement.setAttribute("data-theme", saved); } catch(e){}
function labelThemeButton(){
  var btn = document.getElementById("theme");
  var dark = document.documentElement.getAttribute("data-theme") === "dark";
  btn.setAttribute("aria-label", btn.getAttribute(dark ? "data-to-light" : "data-to-dark"));
}
labelThemeButton();
