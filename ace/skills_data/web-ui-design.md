---
name: web-ui-design
summary: Build a distinctive, accessible, responsive web page with a clear visual hierarchy.
triggers: html, css, landing page, website, web page, webpage, ui, ux, frontend, front-end, layout, dashboard, component, responsive, design, navbar, hero
kind: html
---
Design rules for this task:
- Pick one clear visual idea (a palette of at most 3 colors plus neutrals, one display font stack, one accent) and use it everywhere. Avoid generic purple-gradient defaults.
- Real content only. No lorem ipsum, no placeholder names, no "TODO".
- Structure: lang attribute on html, a title, a viewport meta tag, one h1, then h2 sections inside main. Use semantic elements (header, nav, main, section, footer).
- Responsive: mobile-first CSS, fluid type with clamp(), layout that works at 390px wide and at 1280px wide. No horizontal scroll.
- Accessibility: text contrast of at least 4.5:1, visible focus styles, alt text on every image, buttons are buttons and links are links with real href values.
- One self-contained file: inline CSS, no external requests, no build step.
Return the complete file in a single ```html block, then at most three lines of notes.
