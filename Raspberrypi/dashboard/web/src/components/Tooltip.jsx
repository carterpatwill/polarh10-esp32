import { useEffect } from "react";

// Global hover tooltip for any element with a [data-tip] attribute (native title=
// is slow/unreliable). One document-level listener covers everything React renders,
// so the recovery-table header tips work exactly like the old dashboard.
export default function Tooltip() {
  useEffect(() => {
    const tip = document.createElement("div");
    tip.id = "tip";
    document.body.appendChild(tip);

    const onOver = (e) => {
      const el = e.target.closest("[data-tip]");
      if (!el) return;
      tip.textContent = el.getAttribute("data-tip");
      const r = el.getBoundingClientRect();
      tip.style.left = Math.round(r.left + r.width / 2) + "px";
      tip.style.top = Math.round(r.bottom + 8) + "px";
      tip.classList.add("show");
    };
    const onOut = (e) => {
      if (e.target.closest("[data-tip]")) tip.classList.remove("show");
    };

    document.addEventListener("mouseover", onOver);
    document.addEventListener("mouseout", onOut);
    return () => {
      document.removeEventListener("mouseover", onOver);
      document.removeEventListener("mouseout", onOut);
      tip.remove();
    };
  }, []);

  return null;
}
