An add-on to create Bas Relief and generate Height Maps in Blender

<img width="1781" height="883" alt="bas_relief_img_1" src="https://github.com/user-attachments/assets/0ff5b192-3d84-41f3-a3da-4c086b03d355" />


#

**Bas Relief & Height Map Generator**

This add-on combines a complete Bas Relief creation workflow with an AI-powered height map generator. It allows you to transform any image into a displaced 3D relief mesh and generate high-quality depth maps using the MiDaS DPT-Hybrid neural network model — all from within Blender.


# Requirements

The Height Map Generator requires the following Python libraries:
- `torch` (PyTorch)
- `torchvision`
- `opencv-python`
- `Pillow`
- `numpy`

Install them via pip before using the Height Map Generator feature.
MiDaS model weights are downloaded automatically at first use (internet connection required).

# Installation

Download the ZIP file.

Open Blender and go to **Edit** > **Preferences** > **Add-ons**.

Click **Install**, select the ZIP file, and click **Install Add-on**.

Enable the add-on by checking the corresponding box.

Access **Bas Relief** in the **N menu** (sidebar) under the **Bas Relief tab**.

# How to Use — Bas Relief

1. **Import Image** : Select the image you want to use as a displacement source.
2. **Run Bas Relief** : Creates a subdivided plane with a Displace modifier automatically configured from your image. Adjust **Subdivision Cuts**, **Subsurf Levels** and **Displace Strength** in the Adjust Last Operation panel.
3. **Create Texture** : Creates a new PBR material with an Image Texture node and assigns it to the active object.
4. **Create Depth Map** : Sets up the compositor pipeline using the **Depth_Map_Comp_GN** node group, creates an orthographic camera and a Suzanne preview mesh for depth visualization. Click **Render** to produce the depth map output.
5. **Render** : Renders the depth map using the configured compositor pipeline.
6. **Save** : Saves the render result to disk.
7. **Delete** : Removes the depth map setup (camera, preview mesh and compositor nodes) to start fresh.

### Display Settings
After clicking **Create Depth Map**, two display options appear:
- **Display Device** : Set to `Display P3` for accurate color space.
- **View Transform** : Set to `Raw` for unprocessed depth output.

# How to Use — Height Map Generator

The Height Map Generator uses MiDaS AI to estimate depth from a single image and produce an optimized height map ready for Bas Relief displacement.

1. **Choose Image** : Select the source image (shared with the Bas Relief Import Image button — select once, use everywhere).
2. **Auto Analyze** : Intelligently analyzes the image and automatically sets all controls to optimal values based on resolution, contrast, sharpness, dynamic range, texture density and background distribution. Also auto-sets the MiDaS inference resolution.
3. **Adjust Controls** if needed:
   - **Subject Offset** : Shifts the depth midpoint up or down to center the subject.
   - **Background Compression** : Flattens background depth relative to the subject.
   - **MiDaS Detail Strength** : Amplifies fine depth details from the AI prediction.
   - **Image Detail Injection** : Blends high-frequency source texture into the depth map.
   - **Face Volume Force** : Reinforces smooth rounded volume on organic surfaces.
4. **Reset** : Resets all controls and resolution to default values.
5. **Generate Height Map** : Runs MiDaS inference in a **background thread** — Blender stays fully responsive during generation. The result is saved next to the source image and automatically assigned to the HeightMap texture and Bas Relief plane.

### File Conflict
If a height map with the same name already exists, a dialog appears with three options:
- **Save as new version** : Saves with a numbered suffix (`.2.png`, `.3.png`…)
- **Overwrite** : Replaces the existing file.
- **Cancel** : Discards the generated result.

# Advanced Options

- **Smart Auto Analysis** : Takes image resolution into account — small images get lower MiDaS resolution and more smoothing; large high-resolution images preserve more detail with less artificial boosting.
- **Background Thread Generation** : MiDaS runs in a separate thread so Blender never freezes during AI inference.
- **Shared Image Path** : The image selected via Import Image or Choose Image is shared between both workflows — no need to select it twice.
- **Multi-Relief Support** : Each click on Run Bas Relief creates an independent plane with its own uniquely named texture (`HeightMap.001`, `HeightMap.002`…) — multiple reliefs in the same project never interfere with each other.
- **Aspect Ratio Matching** : The generated plane automatically matches the image aspect ratio — no more forced square planes.
