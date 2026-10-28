# Smart Insole web interface

## Scene and theme

A researcher checks the laptop beside a walking participant in a bright lab. Use light blue-white
surfaces and high-contrast blue controls, keeping the central footprints large and readable.

## References actually inspected

- https://www.awwwards.com/websites/ui-design/ : gallery HTML retrieved; browser reader timed out.
- https://www.awwwards.com/sites/nova-dental : gallery thumbnail inspected, for intentional sans/serif
  contrast and a single strong focal composition. No dental imagery or promotional hero is copied.
- https://www.awwwards.com/sites/smithsonian-institution : gallery thumbnail inspected, for restrained
  blue, clear navigation, and distinct content hierarchy. No institutional branding is copied.
- ../mbugm-website/src/app/layout.tsx and page.module.css: local typography and deliberate button geometry.
- CAPSTONEKOTLIN SensorLayout.kt: exact eight electrical channels and mirrored footprint coordinates.

Thumbnail research files are in design-references/. They are not shipped to the browser.

## Tokens and composition

Blue #2465e8, deep ink #173451, pale blue #edf4fe, surface #fbfdff, muted #61738a,
signal teal #117b75. CSS uses equivalent OKLCH values. Heatmaps use a shared fixed response scale.
Plus Jakarta Sans for controls/data; Fraunces only for the page title. Both fonts are local WOFF2
assets reused from MBUGM with upstream OFL licenses.

Compact rail + working header; main visual monitor alongside a narrow session/control column.
The monitor contains paired footprint heatmaps followed by a selected-channel graph.
Calibration lives in an inline section with two buttons. Sessions and results have their own views.
Use 8px action radii, 16px section radii, limited outlines and no nested decorative cards.

Sketch:

    navigation | title + connection state                  | receive action
               | left footprint  /  right footprint        | session fields
               | shared sensor response scale              | record / stop
               | selected-channel timeline                 | two calibrations

## State and motion

No fake data in hardware mode. Demo is explicit and uses actual loopback UDP.
Status polls at 5 Hz; chart refresh can be paused. Only interaction feedback transitions.
All sensor regions are keyboard selectable and show numeric values. Errors and calibration instructions
are inline. Technical metadata, units and the legacy model limitation are available in details.

## Brand mark

Use the existing Android launcher insole/S/plus geometry. The laptop rail uses
web/assets/insole-white.svg with a genuine transparent plus cutout; the mobile
header uses the original indigo/teal colors. Preserve the launcher artwork.
