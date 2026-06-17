# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

# -----------------------------------------------------------------------
# Height Map Generator (MiDaS DPT-Hybrid) — integrated into Bas Relief
# -----------------------------------------------------------------------
# THIRD-PARTY CREDITS
# MiDaS by Intel ISL — MIT License — https://github.com/isl-org/MiDaS
# PyTorch by Meta Platforms — BSD License — https://pytorch.org
# Model weights are downloaded at runtime via torch.hub (not bundled).
# -----------------------------------------------------------------------

import os
import threading
import bpy
from bpy.props import StringProperty, IntProperty, FloatProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper

from .bas_relief_tools import SHARED_IMAGE_PROP

_MIDAS_CACHE  = None
_gen_thread   = None
_gen_result   = {}   # keys: 'output_path', 'error', 'done'


# -----------------------------------------------------------------------
# Lazy imports
# -----------------------------------------------------------------------
def _lazy_imports():
    import torch
    import numpy as np
    import cv2
    from PIL import Image
    from torchvision import transforms
    return torch, np, cv2, Image, transforms


# -----------------------------------------------------------------------
# Model loader (cached)
# -----------------------------------------------------------------------
def load_midas_model():
    global _MIDAS_CACHE
    if _MIDAS_CACHE:
        return _MIDAS_CACHE
    torch, *_ = _lazy_imports()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    try:
        model = torch.hub.load("intel-isl/MiDaS", "DPT_Hybrid")
    except Exception as e:
        raise RuntimeError(
            f"Could not download MiDaS model: {e}. "
            "Check your internet connection and try again."
        ) from e
    model.to(device)
    model.eval()
    _MIDAS_CACHE = (model, device)
    return _MIDAS_CACHE


# -----------------------------------------------------------------------
# Image preparation
# -----------------------------------------------------------------------
def prepare_image(path, max_dim):
    _, _, _, Image, _ = _lazy_imports()
    img = Image.open(path).convert("RGB")
    w, h  = img.size
    scale = min(max_dim / max(w, h), 1.0)
    nw, nh = int(w * scale), int(h * scale)
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    pw = (32 - nw % 32) % 32
    ph = (32 - nh % 32) % 32
    padded = Image.new("RGB", (nw + pw, nh + ph))
    padded.paste(resized, (0, 0))
    return padded


# -----------------------------------------------------------------------
# Core generation (runs in worker thread — no bpy calls allowed here)
# -----------------------------------------------------------------------
def _run_generation(image_path, max_dim, scene_props):
    global _gen_result
    _gen_result = {'done': False, 'output_path': None, 'error': None}
    try:
        torch, np, cv2, Image, transforms = _lazy_imports()
        model, device = load_midas_model()
        image = prepare_image(image_path, max_dim)

        transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize([0.5]*3, [0.5]*3),
        ])
        input_tensor = transform(image).unsqueeze(0).to(device)

        with torch.no_grad():
            prediction = model(input_tensor)
            prediction = torch.nn.functional.interpolate(
                prediction.unsqueeze(1), size=image.size[::-1],
                mode="bicubic", align_corners=False
            ).squeeze().cpu().numpy()

        depth = prediction
        depth = (depth - depth.min()) / (depth.max() - depth.min() + 1e-8)
        depth = np.clip(depth, 0, 1)

        # Background compression
        gx = cv2.Sobel(depth, cv2.CV_32F, 1, 0, 3)
        gy = cv2.Sobel(depth, cv2.CV_32F, 0, 1, 3)
        grad = np.sqrt(gx*gx + gy*gy)
        grad /= grad.max() + 1e-6
        grad  = np.minimum(grad, 0.1)
        depth = np.clip(depth - (1.0 - grad) * scene_props['relax_factor'], 0, 1)

        # Rebase on subject
        depth = np.clip(depth - np.mean(depth) + 0.5 + scene_props['subject_offset'], 0, 1)

        # Adaptive gamma + detail
        contrast = np.std(depth)
        gamma    = np.clip(0.9 - contrast * 0.3, 0.8, 0.95)
        fg       = cv2.Sobel(depth, cv2.CV_32F, 1, 1, 3)
        freq     = np.mean(np.abs(fg))
        blur     = int(np.clip(5 + freq * 30, 5, 11))
        detail   = np.clip(1.2 + freq * 2.5, 1.1, 1.6)
        depth    = np.power(depth, gamma)
        k        = int(blur * 1.5) * 2 + 1
        low      = cv2.GaussianBlur(depth, (k, k), 0)
        depth    = np.clip(low + (depth - low) * detail * scene_props['detail_boost'], 0, 1)

        # Image detail injection
        gray  = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        low_f = cv2.GaussianBlur(gray, (0, 0), 2)
        high_f = cv2.GaussianBlur(gray - low_f, (0, 0), 1) * scene_props['image_detail_boost']
        depth += high_f * 0.35

        # Face volume
        gx2 = cv2.Sobel(depth, cv2.CV_32F, 1, 0, 5)
        gy2 = cv2.Sobel(depth, cv2.CV_32F, 0, 1, 5)
        g2  = np.sqrt(gx2*gx2 + gy2*gy2)
        g2 /= g2.max() + 1e-6
        mask   = np.clip(cv2.GaussianBlur(g2, (0, 0), 15) * scene_props['face_force'], 0, 1)
        volume = cv2.GaussianBlur(depth, (0, 0), 12)
        depth  = np.clip(depth * (1 - mask) + volume * mask, 0, 1)

        # Final smoothing
        smoothed = cv2.bilateralFilter((depth * 255).astype(np.uint8), 0, 20, 20) / 255.0
        depth    = np.clip(depth * 0.75 + smoothed * 0.25, 0, 1)

        output_path = os.path.join(
            os.path.dirname(image_path),
            "height_map_midas_basrelief.png"
        )
        # Store pixels as bytes in memory — saving is done in the main thread
        # so we can show a popup if the file already exists
        pixels_bytes = (depth * 255).astype(np.uint8).tobytes()
        img_size = (image.size[0], image.size[1])  # (width, height)
        _gen_result = {
            'done': True,
            'output_path': output_path,
            'pixels': pixels_bytes,
            'img_size': img_size,
            'error': None,
        }

    except Exception as e:
        _gen_result = {'done': True, 'output_path': None, 'pixels': None, 'error': str(e)}


# -----------------------------------------------------------------------
# Helper: save pixels to disk and assign to Blender
# -----------------------------------------------------------------------
def _save_and_assign(output_path, pixels_bytes, img_size):
    """Save depth map pixels to disk and assign to HeightMap texture + shared prop."""
    try:
        _, np, _, Image, _ = _lazy_imports()
        arr = np.frombuffer(pixels_bytes, dtype=np.uint8).reshape((img_size[1], img_size[0]))
        Image.fromarray(arr).save(output_path)
    except Exception as e:
        return str(e)

    try:
        scene = bpy.context.scene
        setattr(scene, SHARED_IMAGE_PROP, output_path)
        img = bpy.data.images.load(output_path, check_existing=False)
        tex = bpy.data.textures.get("HeightMap")
        if tex:
            tex.image = img
        obj = next(
            (o for o in scene.objects
             if any(getattr(m, "type", "") == 'DISPLACE' for m in o.modifiers)),
            None
        )
        if obj:
            for mod in obj.modifiers:
                if getattr(mod, "type", "") == 'DISPLACE' and tex:
                    mod.texture = tex
                    break
    except Exception:
        pass
    return None


# -----------------------------------------------------------------------
# Single dialog operator — centered, fixed width, 3 choices
# -----------------------------------------------------------------------
class HEIGHTMAP_OT_FileConflict(bpy.types.Operator):
    """Shown when the output height map file already exists."""
    bl_idname  = "heightmap.file_conflict"
    bl_label   = "File Already Exists"
    bl_options = {'INTERNAL'}

    output_path:    bpy.props.StringProperty()
    versioned_path: bpy.props.StringProperty()
    choice: bpy.props.EnumProperty(
        name="Action",
        items=[
            ('OVERWRITE', "Overwrite",       "Replace the existing file"),
            ('VERSION',   "Save as new version", "Save with a numbered suffix"),
            ('CANCEL',    "Cancel",           "Discard the generated height map"),
        ],
        default='VERSION',
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="A file with this name already exists:", icon='ERROR')
        col.label(text=os.path.basename(self.output_path))
        col.separator()
        col.prop(self, "choice", expand=True)

    def execute(self, context):
        global _gen_result
        if self.choice == 'CANCEL':
            _gen_result = {}
            self.report({'INFO'}, "Height map discarded")
            return {'CANCELLED'}

        pixels = _gen_result.get('_pending_pixels')
        size   = _gen_result.get('_pending_size')
        _gen_result = {}

        if not pixels or not size:
            self.report({'ERROR'}, "No pending height map data")
            return {'CANCELLED'}

        path = self.output_path if self.choice == 'OVERWRITE' else self.versioned_path
        err  = _save_and_assign(path, pixels, size)
        if err:
            self.report({'ERROR'}, f"Could not save: {err}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Saved: {os.path.basename(path)}")
        return {'FINISHED'}



# -----------------------------------------------------------------------
# Timer callback: poll thread completion and handle file conflict
# -----------------------------------------------------------------------
def _check_generation_done():
    global _gen_thread, _gen_result

    if not _gen_result.get('done', False):
        return 0.25  # check again in 250ms

    _gen_thread  = None
    output_path  = _gen_result.get('output_path')
    pixels       = _gen_result.get('pixels')
    img_size     = _gen_result.get('img_size')
    error        = _gen_result.get('error')

    if error:
        _gen_result = {}
        def _report_err():
            bpy.context.window_manager.popup_menu(
                lambda self, ctx: self.layout.label(text=f"Height Map Error: {error}"),
                title="Height Map Generator", icon='ERROR'
            )
        bpy.app.timers.register(_report_err, first_interval=0.0)
        return None

    if not output_path or not pixels:
        _gen_result = {}
        return None

    # Build versioned path: height_map_midas_basrelief.2.png, .3.png …
    base, ext = os.path.splitext(output_path)
    v = 2
    versioned = f"{base}.{v}{ext}"
    while os.path.exists(versioned):
        v += 1
        versioned = f"{base}.{v}{ext}"

    if os.path.exists(output_path):
        # File conflict — stash pixels for the dialog operator and invoke it
        _gen_result['_pending_pixels'] = pixels
        _gen_result['_pending_size']   = img_size

        def _show_dialog():
            try:
                op = bpy.ops.heightmap.file_conflict
                op('INVOKE_DEFAULT',
                   output_path=output_path,
                   versioned_path=versioned)
            except Exception:
                pass
        bpy.app.timers.register(_show_dialog, first_interval=0.0)
        return None

    # No conflict — save immediately
    _gen_result = {}
    err = _save_and_assign(output_path, pixels, img_size)
    if err:
        def _report_err2():
            bpy.context.window_manager.popup_menu(
                lambda self, ctx: self.layout.label(text=f"Save Error: {err}"),
                title="Height Map Generator", icon='ERROR'
            )
        bpy.app.timers.register(_report_err2, first_interval=0.0)
    else:
        def _success():
            bpy.context.window_manager.popup_menu(
                lambda self, ctx: self.layout.label(
                    text=f"Saved: {os.path.basename(output_path)} — image path updated"),
                title="Height Map Generator", icon='CHECKMARK'
            )
        bpy.app.timers.register(_success, first_interval=0.0)

    return None


# -----------------------------------------------------------------------
# Operators
# -----------------------------------------------------------------------
class HEIGHTMAP_OT_ChooseImage(bpy.types.Operator, ImportHelper):
    bl_idname    = "heightmap.choose_image"
    bl_label     = "Choose Image"
    bl_description = ("Select the source image for height map generation. "
                      "The path is shared with the Bas Relief Import Image button")
    filename_ext = ".png;.jpg;.jpeg;.bmp"
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;.bmp", options={'HIDDEN'})

    def execute(self, context):
        setattr(context.scene, SHARED_IMAGE_PROP, self.filepath)
        self.report({'INFO'}, f"Image selected: {os.path.basename(self.filepath)}")
        return {'FINISHED'}


class HEIGHTMAP_OT_Generate(bpy.types.Operator):
    bl_idname    = "heightmap.generate"
    bl_label     = "Generate Height Map"
    bl_description = (
        "Generate a high-quality depth-based height map from the selected image "
        "using MiDaS DPT-Hybrid (requires PyTorch). Runs in a background thread — "
        "Blender stays responsive. The result is saved next to the source image "
        "and automatically assigned to the HeightMap texture and Bas Relief image path"
    )

    def execute(self, context):
        global _gen_thread, _gen_result

        if _gen_thread is not None and _gen_thread.is_alive():
            self.report({'WARNING'}, "Height map generation already running. Please wait.")
            return {'CANCELLED'}

        scene      = context.scene
        image_path = getattr(scene, SHARED_IMAGE_PROP, "")
        if not image_path or not os.path.exists(image_path):
            self.report({'ERROR'}, "No valid image selected. Use Choose Image or Import Image first.")
            return {'CANCELLED'}

        # Snapshot scene props (thread-safe — no bpy access in thread)
        scene_props = {
            'subject_offset':     scene.subject_offset,
            'relax_factor':       scene.relax_factor,
            'detail_boost':       scene.detail_boost,
            'image_detail_boost': scene.image_detail_boost,
            'face_force':         scene.face_force,
        }
        max_dim = scene.heightmap_resolution

        _gen_result = {'done': False}
        _gen_thread = threading.Thread(
            target=_run_generation,
            args=(image_path, max_dim, scene_props),
            daemon=True,
        )
        _gen_thread.start()

        bpy.app.timers.register(_check_generation_done, first_interval=0.25)
        self.report({'INFO'}, "Height map generation started in background. Blender stays responsive.")
        return {'FINISHED'}


# -----------------------------------------------------------------------
# Panels
# -----------------------------------------------------------------------
class HEIGHTMAP_PT_Panel(bpy.types.Panel):
    bl_idname     = "HEIGHTMAP_PT_panel"
    bl_label      = "Height Map Generator"
    bl_space_type = "VIEW_3D"
    bl_region_type= "UI"
    bl_category   = "Bas Relief"
    bl_order      = 10

    def draw(self, context):
        layout = self.layout
        scene  = context.scene
        global _gen_thread

        # Show the shared image path (same as Import Image)
        layout.operator("heightmap.choose_image", icon="IMAGE_DATA")
        layout.prop(scene, SHARED_IMAGE_PROP, text="")
        layout.prop(scene, "heightmap_resolution")

        if _gen_thread is not None and _gen_thread.is_alive():
            layout.label(text="Generating… please wait", icon='TIME')


class HEIGHTMAP_OT_ResetControls(bpy.types.Operator):
    bl_idname    = "heightmap.reset_controls"
    bl_label     = "Reset to Defaults"
    bl_description = "Reset all Height Map Controls sliders to their default values"
    bl_options   = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        scene.heightmap_resolution = 2048
        scene.subject_offset       = 0.0
        scene.relax_factor         = 0.35
        scene.detail_boost         = 1.0
        scene.image_detail_boost   = 1.0
        scene.face_force           = 0.6
        self.report({'INFO'}, "Height Map Controls reset to defaults")
        return {'FINISHED'}


class HEIGHTMAP_OT_AutoAnalyze(bpy.types.Operator):
    bl_idname    = "heightmap.auto_analyze"
    bl_label     = "Auto Analyze Image"
    bl_description = (
        "Intelligently analyze the selected image and automatically set all "
        "Height Map Controls to optimal values. Takes into account resolution, "
        "contrast, sharpness, dynamic range, texture density and background "
        "distribution for the most accurate height map possible"
    )
    bl_options   = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene      = context.scene
        image_path = getattr(scene, SHARED_IMAGE_PROP, "")
        if not image_path or not os.path.exists(image_path):
            self.report({'ERROR'}, "No valid image selected. Use Choose Image first.")
            return {'CANCELLED'}

        try:
            _, np, cv2, Image, _ = _lazy_imports()
        except Exception as e:
            self.report({'ERROR'}, f"Could not import analysis libraries: {e}")
            return {'CANCELLED'}

        try:
            pil_full = Image.open(image_path).convert("RGB")
            orig_w, orig_h = pil_full.size
        except Exception as e:
            self.report({'ERROR'}, f"Could not open image: {e}")
            return {'CANCELLED'}

        # ----------------------------------------------------------------
        # RESOLUTION METRICS
        # ----------------------------------------------------------------
        megapixels   = (orig_w * orig_h) / 1_000_000.0
        aspect_ratio = orig_w / max(orig_h, 1)

        # Score 0..1: how "high-res" the image is (saturates at 8MP)
        res_score = float(np.clip(megapixels / 8.0, 0.0, 1.0))

        # Auto-set MiDaS resolution based on image size
        # Small (<1MP) → 512, Medium (1-4MP) → 1024, Large (>4MP) → 2048
        if megapixels < 1.0:
            optimal_resolution = 512
        elif megapixels < 4.0:
            optimal_resolution = 1024
        else:
            optimal_resolution = 2048

        # ----------------------------------------------------------------
        # CONTENT METRICS — analyse on a fast thumbnail
        # ----------------------------------------------------------------
        pil_thumb = pil_full.copy()
        pil_thumb.thumbnail((512, 512), Image.Resampling.LANCZOS)
        arr  = np.array(pil_thumb).astype(np.float32) / 255.0
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

        # Brightness and contrast
        mean_brightness = float(np.mean(gray))
        contrast        = float(np.std(gray))

        # Dynamic range: spread between dark and bright areas
        p5, p95      = float(np.percentile(gray, 5)), float(np.percentile(gray, 95))
        dynamic_range = p95 - p5  # 0=flat, 1=full range

        # Sharpness via Laplacian variance
        lap           = cv2.Laplacian(gray, cv2.CV_32F)
        sharpness_raw = float(np.var(lap))
        sharpness     = float(np.clip(sharpness_raw / 500.0, 0.0, 1.0))

        # Local texture variance (small patches) — detects fine surface texture
        kernel        = np.ones((8, 8), np.float32) / 64.0
        local_mean    = cv2.filter2D(gray, -1, kernel)
        local_var     = cv2.filter2D((gray - local_mean)**2, -1, kernel)
        texture_score = float(np.clip(np.mean(local_var) * 20.0, 0.0, 1.0))

        # Background ratio
        bg_ratio      = float(np.mean(gray < mean_brightness * 0.6))

        # Edge density
        sx  = cv2.Sobel(gray, cv2.CV_32F, 1, 0, 3)
        sy  = cv2.Sobel(gray, cv2.CV_32F, 0, 1, 3)
        edge_density  = float(np.mean(np.sqrt(sx**2 + sy**2)))

        # Portrait bias: tall images tend to be faces/subjects
        portrait_bias = float(np.clip(1.0 / max(aspect_ratio, 0.1) - 1.0, 0.0, 1.0))

        # ----------------------------------------------------------------
        # INTELLIGENT PARAMETER ESTIMATION
        # ----------------------------------------------------------------

        # subject_offset: dark images → push up; portrait → slight push up
        subject_offset = float(np.clip(
            (0.5 - mean_brightness) * 0.5 + portrait_bias * 0.05,
            -0.3, 0.3
        ))

        # relax_factor: dark bg + low dynamic range → compress more
        relax_factor = float(np.clip(
            0.15 + bg_ratio * 0.4 + (1.0 - dynamic_range) * 0.2,
            0.1, 0.7
        ))

        # detail_boost: blurry OR low-res → boost more; high-res sharp → less
        # High res already has detail, no need to over-amplify
        detail_boost = float(np.clip(
            1.8 - sharpness * 0.6 - res_score * 0.3 + (1.0 - dynamic_range) * 0.2,
            0.6, 2.2
        ))

        # image_detail_boost: fine texture + high res → inject more detail
        # Low res → reduce injection to avoid noise amplification
        image_detail_boost = float(np.clip(
            0.3 + edge_density * 2.5 + texture_score * 0.8 + res_score * 0.4,
            0.3, 2.5
        ))

        # face_force: organic/portrait + low sharpness → more smoothing
        # High res + high sharpness → less smoothing to preserve detail
        face_force = float(np.clip(
            0.2 + contrast * 0.5 + portrait_bias * 0.3 +
            (1.0 - sharpness) * 0.3 - res_score * 0.1,
            0.2, 1.2
        ))

        # Apply all values
        scene.subject_offset        = round(subject_offset, 3)
        scene.relax_factor          = round(relax_factor, 3)
        scene.detail_boost          = round(detail_boost, 3)
        scene.image_detail_boost    = round(image_detail_boost, 3)
        scene.face_force            = round(face_force, 3)
        scene.heightmap_resolution  = optimal_resolution

        self.report(
            {'INFO'},
            f"Auto-analyzed {orig_w}×{orig_h}px ({megapixels:.1f}MP) — "
            f"resolution set to {optimal_resolution} | "
            f"contrast={contrast:.2f} sharpness={sharpness:.2f} "
            f"texture={texture_score:.2f} dynrange={dynamic_range:.2f}"
        )
        return {'FINISHED'}


class HEIGHTMAP_PT_Controls(bpy.types.Panel):
    bl_label      = "Height Map Controls"
    bl_parent_id  = "HEIGHTMAP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type= "UI"
    bl_options    = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        # Action buttons row
        row = layout.row(align=True)
        row.operator("heightmap.auto_analyze", text="Auto Analyze", icon='SHADERFX')
        row.operator("heightmap.reset_controls", text="", icon='LOOP_BACK')

        layout.separator()
        layout.prop(scene, "subject_offset",     slider=True)
        layout.prop(scene, "relax_factor",       slider=True)
        layout.separator()
        layout.prop(scene, "detail_boost",       slider=True)
        layout.prop(scene, "image_detail_boost", slider=True)
        layout.prop(scene, "face_force",         slider=True)


class HEIGHTMAP_PT_Generate(bpy.types.Panel):
    bl_label      = "Generate"
    bl_parent_id  = "HEIGHTMAP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type= "UI"
    bl_options    = {'DEFAULT_CLOSED'}

    def draw(self, context):
        self.layout.operator("heightmap.generate", text="Generate Height Map", icon="RENDER_STILL")


# -----------------------------------------------------------------------
# Register
# -----------------------------------------------------------------------
classes = (
    HEIGHTMAP_OT_ChooseImage,
    HEIGHTMAP_OT_Generate,
    HEIGHTMAP_OT_FileConflict,
    HEIGHTMAP_OT_ResetControls,
    HEIGHTMAP_OT_AutoAnalyze,
    HEIGHTMAP_PT_Panel,
    HEIGHTMAP_PT_Controls,
    HEIGHTMAP_PT_Generate,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.heightmap_resolution = IntProperty(
        name="Resolution",
        description="Maximum resolution for MiDaS inference (higher = more detail but slower)",
        default=2048, min=512, max=4096,
    )
    bpy.types.Scene.subject_offset = FloatProperty(
        name="Subject Offset",
        description="Shift the depth midpoint up (+) or down (-)",
        default=0.0, soft_min=-0.5, soft_max=0.5,
    )
    bpy.types.Scene.relax_factor = FloatProperty(
        name="Background Compression",
        description="How much to flatten background depth relative to the subject",
        default=0.35, min=0.0, max=1.0,
    )
    bpy.types.Scene.detail_boost = FloatProperty(
        name="MiDaS Detail Strength",
        description="Amplify fine depth details from the MiDaS prediction",
        default=1.0, min=0.0, max=2.5,
    )
    bpy.types.Scene.image_detail_boost = FloatProperty(
        name="Image Detail Injection",
        description="Blend high-frequency image texture into the depth map",
        default=1.0, min=0.0, max=3.0,
    )
    bpy.types.Scene.face_force = FloatProperty(
        name="Face Volume Force",
        description="Reinforce smooth rounded volume on facial or curved surfaces",
        default=0.6, min=0.0, max=1.5,
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    for prop in ("heightmap_resolution", "subject_offset", "relax_factor",
                 "detail_boost", "image_detail_boost", "face_force"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
