# CADia — Modeling Evidence

This directory contains CAD modeling examples paired with the exact English prompts used for each case. Each case includes its prompt and the exported artifacts produced by that run.

## Repository structure

Each case uses the same naming convention where the corresponding artifact exists:

- `prompt.txt` — exact prompt for the case
- `preview.png` — screenshot of the generated result
- `model.step` — STEP export
- `model-ap242.step` — STEP AP242 export
- `model.stl` — STL export
- `model.3mf` — 3MF export
- `toolpath.gcode` — PrusaSlicer-generated 3D-print G-code, where available

## Modeling examples

| Case | Prompt |
| --- | --- |
| [Spur gear](./01-spur-gear/) | Create an external spur gear with module 2, 24 teeth, a pressure angle of 20°, and a face width of 15 mm. Create a Ø12 mm through bore at the center. |
| [Helical gear](./02-helical-gear/) | Create a right-hand helical gear with module 2, 30 teeth, a pressure angle of 20°, a helix angle of 20°, and a face width of 18 mm. Create a 15 mm bore at the center. |
| [Spur gear assembly](./03-spur-gear-assembly/) | Create two spur gears with module 2 and 20 and 40 teeth respectively as separate parts, position them at the correct center distance, and assemble them so that they mesh with each other. The axes of the two gears must be parallel. |
| [Bearing housing](./04-bearing-housing/) | Create a simple bearing housing that can accommodate a 6204 ball bearing with an outer diameter of 47 mm and a width of 14 mm. Create a cylindrical seat for the bearing outer ring and retaining flanges on both sides, and place two mounting holes in the base for M8 bolts. |
| [Ball bearing](./05-ball-bearing/) | Create a single-row ball bearing with an inner diameter of 20 mm, an outer diameter of 47 mm, and a width of 14 mm. Arrange 8 balls at equal intervals. |
| [Stepped shaft](./06-stepped-shaft/) | Create a rotating shaft with an overall length of 180 mm. The diameter of the central 80 mm section should be 35 mm, and the bearing mounting sections on both sides should have a diameter of 25 mm. Create M20 external threads on both ends, and apply appropriate shaft shoulders and 2 mm fillets to the stepped sections. |
| [Eccentric disc cam](./07-eccentric-disc-cam/) | Create a disc cam with a diameter of 60 mm and a thickness of 12 mm. The shaft hole should have a diameter of 12 mm and be positioned with an eccentricity of 8 mm from the center of the disc. Add a 4 mm wide keyway to the shaft hole and apply a 1 mm chamfer to the outer edge. |
| [Bevel gear assembly](./08-bevel-gear-assembly/) | Create one straight bevel gear for two shafts intersecting at 90°. Use module 2, 24 teeth, a pressure angle of 20°, and a pitch cone angle of 45°. Create another bevel gear of the same size and assemble them so that they mesh at 90°. The center bore diameter should be 12 mm. |
| [Worm](./09-worm/) | Create a single-start right-hand worm for a shaft diameter of approximately 20 mm. Use an axial module of 2 mm and a pressure angle of 20°, and make the total worm length 70 mm. Add cylindrical shafts 20 mm long to both sides. |
| [U-shaped bracket](./10-u-shaped-bracket/) | Create a U-shaped bracket. The base is 100 mm long, 60 mm wide and 8 mm thick. Add two 8 mm thick, 50 mm high side walls. Create Ø20 through holes in the two walls that share the same axis. |
| [Sprocket](./11-sprocket/) | Create a sprocket with a chain pitch of 12.7 mm, 20 teeth, a thickness of 8 mm, and a center bore of Ø20 mm. |
| [Extension spring](./13-extension-spring/) | Create an extension spring with a wire diameter of 2.5 mm, a coil diameter of 25 mm, and a body length of 70 mm. |
| [V-belt pulley](./15-v-belt-pulley/) | Create a double-groove V-belt pulley with an outer diameter of 100 mm, a width of 32 mm, and a shaft bore of Ø20 mm. The V-groove angle should be 40°, and the central hub should have an outer diameter of 40 mm and a length of 35 mm. Add a 6 mm keyway to the shaft bore. |
| [Flexible coupling](./17-flexible-coupling/) | Create a flexible coupling that connects two shafts with a diameter of 10 mm. Make the outer diameter 25 mm and the overall length 30 mm. |
| [Rack and pinion](./19-rack-and-pinion/) | Create a straight rack gear with module 2, a pressure angle of 20°, a length of 200 mm, a width of 20 mm, and a height of 20 mm. Create a standard spur pinion with module 2, 20 teeth, a pressure angle of 20°, a face width of 15 mm, and a center bore of Ø12 mm, and assemble them together. |
| [Pencil sharpener](./21-pencil-sharpener/) | Model a simple pencil sharpener and determine appropriate dimensions. |
| [Mechanical pencil](./22-mechanical-pencil/) | Model a simple mechanical pencil shape and determine appropriate dimensions. Also add a mechanical pencil clip and attach it to the body. |
| [Laptop assembly](./23-laptop-assembly/) | Model a laptop and assemble it so that it can functionally open and close. |
| [Clock assembly](./24-clock-assembly/) | Create a clock and assemble the hour hand, second hand, and minute hand so that all three can rotate properly. Also add the numbers 1 through 12 to the clock. |
| [Tumbler assembly](./25-tumbler-assembly/) | Model a tumbler. I would also like a handle on the side. Model the tumbler lid properly as well and assemble it. |

## Notes

These examples document CADia modeling runs and their resulting artifacts.

- All directory names and public-facing filenames are in English.
- Exported CAD and manufacturing files are preserved from saved modeling runs; public-facing filenames and directory organization have been normalized.
- Prompt text is duplicated inside each case directory to keep every example self-contained.
