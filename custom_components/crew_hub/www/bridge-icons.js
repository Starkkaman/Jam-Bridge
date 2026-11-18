// The Bridge icon set for Home Assistant: "bridge:captain-chair" (sidebar panel_icon of The Bridge app).
// Loaded on every page via extra_js_url; registers HA's customIconsets prefix "bridge". 24-unit MDI-style paths.
(() => {
  const ICONS = {
    "captain-chair": "M7 2H17V11H7ZM2 8H6V12H18V8H22V16H2ZM19 9V11H21V9ZM9 16H15V19H9ZM3 19H21L23 22H1Z",
  };
  const sets = (window.customIconsets = window.customIconsets || {});
  sets.bridge = async (name) => ({ path: ICONS[name] || "", viewBox: "0 0 24 24" });
})();
