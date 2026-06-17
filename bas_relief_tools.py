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

import bpy
import bpy.utils.previews
import os
import tempfile
import math
from bpy_extras.io_utils import ImportHelper

addon_keymaps = {}
_icons = None

# Shared image path property name used by both Bas Relief and Height Map modules
SHARED_IMAGE_PROP = "bas_relief_image_path"

NODE_GROUP_NAME  = "Depth_Map_Comp_GN"
ASSET_BLEND_NAME = "Compositor Nodes Depth Map_2.blend"

_pending_redo_override = None


def _call_redo_panel_timer():
    global _pending_redo_override
    try:
        if _pending_redo_override:
            try:
                bpy.ops.wm.redo_last(_pending_redo_override)
            except Exception:
                try:
                    bpy.ops.wm.redo_last()
                except Exception:
                    pass
        else:
            try:
                bpy.ops.wm.redo_last()
            except Exception:
                pass
    except Exception:
        pass
    _pending_redo_override = None
    return None


def _apply_display_defaults_to_scene(scene):
    try:
        scene.display_settings.display_device = 'Display P3'
    except Exception:
        try:
            scene.display_settings.display_device = 'sRGB'
        except Exception:
            pass
    for transform in ('Raw', 'AgX', 'Standard'):
        try:
            scene.view_settings.view_transform = transform
            break
        except Exception:
            continue
    try:
        for w in bpy.context.window_manager.windows:
            try:
                w.scene = scene
            except Exception:
                pass
            try:
                for a in w.screen.areas:
                    a.tag_redraw()
            except Exception:
                pass
    except Exception:
        pass


def find_asset_path():
    try:
        candidate = os.path.join(os.path.dirname(__file__), "assets", ASSET_BLEND_NAME)
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass
    try:
        addon_base = os.path.basename(os.path.dirname(__file__))
        for scripts_path in bpy.utils.script_paths():
            candidate = os.path.join(scripts_path, "addons", addon_base, "assets", ASSET_BLEND_NAME)
            if os.path.exists(candidate):
                return candidate
    except Exception:
        pass
    return None


def get_compositor_tree(scene):
    try:
        t = getattr(scene, "node_tree", None)
        if t:
            return t
        comp = getattr(scene, "compositor", None)
        if comp:
            return getattr(comp, "node_tree", None)
    except Exception:
        pass
    return None


def force_all_rlayers_to_scene(target_scene, target_view_layer):
    for sc in bpy.data.scenes:
        t = getattr(sc, "node_tree", None)
        comp = getattr(sc, "compositor", None)
        if not t and comp:
            t = getattr(comp, "node_tree", None)
        if not t:
            continue
        for n in t.nodes:
            if getattr(n, "bl_idname", "") == "CompositorNodeRLayers":
                try:
                    n.scene = target_scene
                except Exception:
                    pass
                try:
                    n.layer = (getattr(target_view_layer, "name", None)
                               or next(iter(target_scene.view_layers)).name)
                except Exception:
                    pass
    for ng in bpy.data.node_groups:
        for n in ng.nodes:
            if getattr(n, "bl_idname", "") == "CompositorNodeRLayers":
                try:
                    n.scene = target_scene
                except Exception:
                    pass
                try:
                    n.layer = (getattr(target_view_layer, "name", None)
                               or next(iter(target_scene.view_layers)).name)
                except Exception:
                    pass
    try:
        for w in bpy.context.window_manager.windows:
            try:
                w.scene = target_scene
            except Exception:
                pass
    except Exception:
        pass


# -------------------------------------------------------------------------
# Main panel
# -------------------------------------------------------------------------
class BASRELIEF_PT_main(bpy.types.Panel):
    bl_label      = 'Bas Relief'
    bl_idname     = 'BASRELIEF_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type= 'UI'
    bl_category   = 'Bas Relief'
    bl_order      = 0

    @classmethod
    def poll(cls, context):
        return context.object is not None or True  # always show; height map panel below

    def draw(self, context):
        layout = self.layout
        layout.operator('basrelief.import_image', text='Import Image', icon='IMAGE_DATA')
        layout.operator('basrelief.run_bas_relief', text='Run Bas Relief', icon='MESH_PLANE')

        row = layout.row(align=True)
        row.operator('basrelief.create_texture', text='Create Texture', icon='MATERIAL')
        row.operator('basrelief.open_texture_image', text='', icon='FILE_FOLDER')

        row2 = layout.row(align=True)
        row2.operator('basrelief.create_depth_map', text='Create Depth Map', icon='NODE_COMPOSITING')
        row2.operator('basrelief.delete_depth_map', text='', icon='TRASH')
        row2.operator('render.render', text='Render', icon='RENDER_STILL')
        row2.operator('basrelief.save_render_image', text='', icon='FILE_TICK')

        if getattr(context.scene, 'bas_relief_depth_map_created', False):
            row3 = layout.row()
            try:
                row3.prop(context.scene.display_settings, 'display_device', text='Display', emboss=True)
                row3.prop(context.scene.view_settings, 'view_transform', text='View', emboss=True)
            except Exception:
                pass


# -------------------------------------------------------------------------
# Import Image — writes to the shared image path prop
# -------------------------------------------------------------------------
class BASRELIEF_OT_import_image(bpy.types.Operator, ImportHelper):
    bl_idname    = "basrelief.import_image"
    bl_label     = "Import Image"
    bl_description = "Select the image to use for Bas Relief displacement"
    filename_ext = ".png;.jpg;.jpeg;.bmp;.tiff"
    filter_glob: bpy.props.StringProperty(default="*.png;*.jpg;*.jpeg;*.bmp;*.tiff", options={'HIDDEN'})
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        setattr(context.scene, SHARED_IMAGE_PROP, self.filepath)
        self.report({'INFO'}, f"Image selected: {os.path.basename(self.filepath)}")
        return {'FINISHED'}


# -------------------------------------------------------------------------
# Run Bas Relief — parameterised subdivisions
# -------------------------------------------------------------------------
class BASRELIEF_OT_run_bas_relief(bpy.types.Operator):
    bl_idname    = "basrelief.run_bas_relief"
    bl_label     = "Run Bas Relief"
    bl_description = ("Create a displaced plane from the imported image. "
                      "Adjust subdivision cuts and subdivision levels before running "
                      "to control mesh density vs. performance")
    bl_options = {"REGISTER", "UNDO"}

    # Exposed as Adjust Last Operation parameters
    subdivision_cuts: bpy.props.IntProperty(
        name="Subdivision Cuts",
        description="Number of cuts applied to the base plane. Higher = more detail but slower",
        default=100, min=10, max=500,
    )
    subsurf_levels: bpy.props.IntProperty(
        name="Subsurf Levels",
        description="Subdivision Surface modifier viewport level",
        default=2, min=0, max=6,
    )
    subsurf_render: bpy.props.IntProperty(
        name="Subsurf Render Levels",
        description="Subdivision Surface modifier render level",
        default=3, min=0, max=6,
    )
    strength: bpy.props.FloatProperty(
        name="Displace Strength",
        description="Strength of the Displace modifier",
        default=0.05, min=0.001, max=2.0, precision=4,
    )
    mid_level: bpy.props.FloatProperty(
        name="Displace Mid Level",
        description="Mid level of the Displace modifier",
        default=0.5, min=0.0, max=1.0, precision=4,
    )

    def _find_area_override(self, context, area_type='VIEW_3D'):
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == area_type:
                    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                    if region is None and area.regions:
                        region = area.regions[0]
                    if region:
                        return {'window': window, 'screen': window.screen,
                                'area': area, 'region': region,
                                'space_data': area.spaces.active}
        return None

    def execute(self, context):
        global _pending_redo_override
        scene  = context.scene
        image_path = getattr(scene, SHARED_IMAGE_PROP, "")
        if not image_path or not os.path.exists(image_path):
            self.report({'WARNING'}, "No valid image selected. Use Import Image first.")
            return {'CANCELLED'}

        # Read image dimensions for aspect ratio
        try:
            img_check = bpy.data.images.load(image_path, check_existing=True)
            img_w, img_h = img_check.size
            if img_h == 0:
                img_w, img_h = 1, 1
        except Exception:
            img_w, img_h = 1, 1
        ratio = img_w / img_h  # > 1 landscape, < 1 portrait, = 1 square

        # Unique name per relief so multiple reliefs never share textures
        idx = 1
        while bpy.data.textures.get(f"HeightMap.{idx:03d}"):
            idx += 1
        tex_name   = f"HeightMap.{idx:03d}"
        plane_name = f"BAS_Relief.{idx:03d}"

        # Use primitive_grid_add for uniform vertex density (no diagonal artefacts)
        cuts  = self.subdivision_cuts
        x_cuts = max(1, int(cuts * ratio)) if ratio >= 1 else cuts
        y_cuts = cuts if ratio >= 1 else max(1, int(cuts / ratio))
        bpy.ops.mesh.primitive_grid_add(
            x_subdivisions=x_cuts,
            y_subdivisions=y_cuts,
            size=2,
            location=(0, 0, 0),
        )
        plane = bpy.context.object
        plane.name  = plane_name
        plane.scale = (ratio, 1.0, 1.0)   # match image aspect ratio

        subsurf = plane.modifiers.new(name="Subdivision", type='SUBSURF')
        subsurf.levels        = self.subsurf_levels
        subsurf.render_levels = self.subsurf_render

        displace = None
        override_area = self._find_area_override(context)
        override = None
        if override_area:
            override = dict(override_area)
            override['scene'] = scene
            try:
                bpy.context.view_layer.objects.active = plane
                bpy.ops.object.modifier_add(override, type='DISPLACE')
                displace = plane.modifiers[-1]
            except Exception:
                displace = None

        if displace is None:
            try:
                bpy.context.view_layer.objects.active = plane
                bpy.ops.object.modifier_add(type='DISPLACE')
                displace = plane.modifiers[-1]
            except Exception:
                displace = plane.modifiers.new(name="Displacement", type='DISPLACE')

        displace.name             = "Displacement"
        displace.strength         = self.strength
        displace.mid_level        = self.mid_level
        displace.show_in_editmode = True
        displace.show_on_cage     = True

        # Unique texture per relief — no shared singleton
        tex = bpy.data.textures.new(tex_name, type='IMAGE')
        try:
            img = bpy.data.images.load(image_path, check_existing=True)
            tex.image        = img
            displace.texture = tex
        except Exception as e:
            self.report({'WARNING'}, f"Could not load image: {e}")
            return {'CANCELLED'}

        # Redo panel timer
        _pending_redo_override = override
        try:
            bpy.app.timers.register(_call_redo_panel_timer, first_interval=0.05)
        except Exception:
            pass

        try:
            mod_index = len(plane.modifiers) - 1
            if override:
                bpy.ops.basrelief.adjust_displace(override, 'INVOKE_DEFAULT',
                                            object_name=plane.name, mod_index=mod_index)
            else:
                bpy.ops.basrelief.adjust_displace('INVOKE_DEFAULT',
                                            object_name=plane.name, mod_index=mod_index)
        except Exception:
            pass

        try:
            _apply_display_defaults_to_scene(scene)
        except Exception:
            pass

        self.report({'INFO'}, "Bas Relief created successfully")
        return {'FINISHED'}


# -------------------------------------------------------------------------
# Create Texture
# -------------------------------------------------------------------------
class BASRELIEF_OT_create_texture(bpy.types.Operator):
    bl_idname    = "basrelief.create_texture"
    bl_label     = "Create Texture"
    bl_description = "Create a new PBR material with an Image Texture node and assign it to the active object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        mat   = bpy.data.materials.new(name="NewMaterialWithTexture")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        for n in list(nodes):
            nodes.remove(n)
        out   = nodes.new(type="ShaderNodeOutputMaterial"); out.location  = (400, 0)
        bsdf  = nodes.new(type="ShaderNodeBsdfPrincipled"); bsdf.location = (0, 0)
        tex   = nodes.new(type="ShaderNodeTexImage");       tex.location  = (-400, 0)
        try:
            links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
        except Exception:
            pass
        try:
            links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
        except Exception:
            pass
        obj = context.object
        if obj:
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)
        return {'FINISHED'}


# -------------------------------------------------------------------------
# Open Texture Image
# -------------------------------------------------------------------------
class BASRELIEF_OT_open_texture_image(bpy.types.Operator, ImportHelper):
    bl_idname    = "basrelief.open_texture_image"
    bl_label     = "Open Texture Image"
    bl_description = "Load an image file and assign it to the active material's Image Texture node"
    filename_ext = ".png;.jpg;.jpeg;.bmp;.tiff"
    filter_glob: bpy.props.StringProperty(default="*.png;*.jpg;*.jpeg;*.bmp;*.tiff", options={'HIDDEN'})
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        fp = self.filepath
        if not fp or not os.path.exists(bpy.path.abspath(fp)):
            self.report({'WARNING'}, "Invalid file path")
            return {'CANCELLED'}
        try:
            img = bpy.data.images.load(fp, check_existing=True)
        except Exception as e:
            self.report({'ERROR'}, f"Could not load image: {e}")
            return {'CANCELLED'}

        obj = context.object
        assigned = False
        if obj and obj.active_material and getattr(obj.active_material, "use_nodes", False):
            mat   = obj.active_material
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            tex_node = next((n for n in nodes if n.type == 'TEX_IMAGE'), None)
            if tex_node is None:
                tex_node = nodes.new(type='ShaderNodeTexImage')
                tex_node.location = (-400, 0)
                bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
                if bsdf:
                    try:
                        links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
                    except Exception:
                        pass
            if tex_node:
                tex_node.image = img
                assigned = True

        if not assigned:
            tex = bpy.data.textures.get("HeightMap") or bpy.data.textures.new("HeightMap", type='IMAGE')
            tex.image = img

        self.report({'INFO'}, f"Image assigned: {os.path.basename(fp)}")
        return {'FINISHED'}


# -------------------------------------------------------------------------
# Adjust Displace popup
# -------------------------------------------------------------------------
class BASRELIEF_OT_adjust_displace(bpy.types.Operator):
    bl_idname    = "basrelief.adjust_displace"
    bl_label     = "Adjust Displace"
    bl_description = "Fine-tune the Displace modifier strength and mid level"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    object_name: bpy.props.StringProperty()
    mod_index:   bpy.props.IntProperty()
    strength:    bpy.props.FloatProperty(name="Strength",  default=0.05, precision=4)
    mid_level:   bpy.props.FloatProperty(name="Midlevel",  default=0.5,  precision=4)

    def invoke(self, context, event):
        obj = bpy.data.objects.get(self.object_name)
        if not obj:
            return {'CANCELLED'}
        try:
            mod = obj.modifiers[self.mod_index]
            self.strength  = getattr(mod, "strength",  0.05)
            self.mid_level = getattr(mod, "mid_level", 0.5)
        except Exception:
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        self.layout.prop(self, "strength")
        self.layout.prop(self, "mid_level")

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if not obj:
            return {'CANCELLED'}
        try:
            mod = obj.modifiers[self.mod_index]
            mod.strength  = self.strength
            mod.mid_level = self.mid_level
        except Exception:
            pass
        return {'FINISHED'}


# -------------------------------------------------------------------------
# Adjust Displace N-panel sub-panel
# -------------------------------------------------------------------------
class BASRELIEF_PT_displace_adjust(bpy.types.Panel):
    bl_label      = "Adjust Displace"
    bl_idname     = "BASRELIEF_PT_displace_adjust"
    bl_space_type = 'VIEW_3D'
    bl_region_type= 'UI'
    bl_parent_id  = 'BASRELIEF_PT_main'
    bl_category   = 'Bas Relief'
    bl_order      = 1

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and any(getattr(m, "type", "") == 'DISPLACE'
                                       for m in getattr(obj, "modifiers", []))

    def draw(self, context):
        layout = self.layout
        obj    = context.object
        mod    = next((m for m in reversed(obj.modifiers) if m.type == 'DISPLACE'), None)
        if mod:
            try:
                layout.prop(mod, "strength",  text="Height Map Strength")
                layout.prop(mod, "mid_level", text="Mid Level")
            except Exception:
                layout.label(text="Cannot display modifier properties")


# -------------------------------------------------------------------------
# Height Map Render preview sub-panel
# -------------------------------------------------------------------------
class BASRELIEF_PT_height_map_render(bpy.types.Panel):
    bl_label      = 'Height Map Render'
    bl_idname     = 'BASRELIEF_PT_height_map_render'
    bl_space_type = 'VIEW_3D'
    bl_region_type= 'UI'
    bl_parent_id  = 'BASRELIEF_PT_main'
    bl_order      = 2

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def draw(self, context):
        layout = self.layout
        tex_to_show = None

        obj = context.object
        if obj:
            for mod in getattr(obj, "modifiers", []):
                try:
                    if mod.type == 'DISPLACE' and getattr(mod, "texture", None):
                        tex_to_show = mod.texture
                        break
                except Exception:
                    pass

        if tex_to_show is None:
            tex_to_show = next((t for t in bpy.data.textures if t.name.startswith("HeightMap")), None)

        render_img = bpy.data.images.get('Render Result')
        if tex_to_show is None and render_img:
            try:
                render_img.preview_ensure()
                layout.template_preview(render_img, show_buttons=False)
            except Exception:
                pass
            return

        if tex_to_show and getattr(tex_to_show, "image", None):
            try:
                tex_to_show.image.preview_ensure()
            except Exception:
                pass
            try:
                layout.template_preview(tex_to_show, show_buttons=False)
            except Exception:
                layout.label(text="Preview error")
        else:
            layout.label(text="No HeightMap available")


# -------------------------------------------------------------------------
# Save Render Image
# -------------------------------------------------------------------------
class BASRELIEF_OT_save_render_image(bpy.types.Operator):
    bl_idname    = "basrelief.save_render_image"
    bl_label     = "Save Render Image"
    bl_description = "Save the current Render Result to a file"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    filepath:    bpy.props.StringProperty(subtype='FILE_PATH', default="")
    filter_glob: bpy.props.StringProperty(default="*.png;*.jpg;*.jpeg;*.exr;*.tiff", options={'HIDDEN'})

    def invoke(self, context, event):
        render_img = bpy.data.images.get('Render Result')
        if render_img is None:
            self.report({'ERROR'}, "No Render Result available to save.")
            return {'CANCELLED'}
        try:
            self.filepath = bpy.path.abspath(context.scene.render.filepath) or ""
        except Exception:
            self.filepath = ""
        if not self.filepath:
            self.filepath = os.path.join(tempfile.gettempdir(), "render_result.png")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        render_img = bpy.data.images.get('Render Result')
        if render_img is None:
            self.report({'ERROR'}, "No Render Result available to save.")
            return {'CANCELLED'}
        target = bpy.path.abspath(self.filepath)
        if not target:
            self.report({'ERROR'}, "No filepath chosen.")
            return {'CANCELLED'}
        try:
            render_img.save_render(target)
            self.report({'INFO'}, f"Saved to: {target}")
            return {'FINISHED'}
        except Exception as e:
            try:
                ext = os.path.splitext(target)[1].lower()
                render_img.filepath_raw = target
                if ext == '.png':   render_img.file_format = 'PNG'
                elif ext == '.exr': render_img.file_format = 'OPEN_EXR'
                elif ext in ('.jpg', '.jpeg'): render_img.file_format = 'JPEG'
                render_img.save()
                self.report({'INFO'}, f"Saved to: {target}")
                return {'FINISHED'}
            except Exception as e2:
                self.report({'ERROR'}, f"Failed to save: {e} / {e2}")
                return {'CANCELLED'}


# -------------------------------------------------------------------------
# Delete Depth Map — remove Suzanne_Auto, Camera_Auto and compositor nodes
# -------------------------------------------------------------------------
class BASRELIEF_OT_delete_depth_map(bpy.types.Operator):
    bl_idname    = "basrelief.delete_depth_map"
    bl_label     = "Delete Depth Map"
    bl_description = ("Remove everything created by Create Depth Map: "
                      "the Suzanne_Auto preview mesh, the Camera_Auto, "
                      "and the compositor node tree of the current scene")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = []

        # Remove Suzanne_Auto and Camera_Auto
        for name in ("Suzanne_Auto", "Camera_Auto"):
            obj = bpy.data.objects.get(name)
            if obj:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed.append(name)

        # Clear the compositor node tree
        scene = context.scene
        try:
            tree = None
            t = getattr(scene, "node_tree", None)
            comp = getattr(scene, "compositor", None)
            if t:
                tree = t
            elif comp:
                tree = getattr(comp, "node_tree", None)
            if tree:
                tree.nodes.clear()
                removed.append("compositor nodes")
        except Exception:
            pass

        # Remove the loaded node group from bpy.data
        ng = bpy.data.node_groups.get(NODE_GROUP_NAME)
        if ng:
            bpy.data.node_groups.remove(ng)
            removed.append(NODE_GROUP_NAME)

        if removed:
            self.report({'INFO'}, f"Deleted: {', '.join(removed)}")
        else:
            self.report({'INFO'}, "Nothing to delete")
        context.scene.bas_relief_depth_map_created = False
        return {'FINISHED'}


# -------------------------------------------------------------------------
# Create Depth Map
# -------------------------------------------------------------------------
class BASRELIEF_OT_create_depth_map(bpy.types.Operator):
    bl_idname    = "basrelief.create_depth_map"
    bl_label     = "Create Depth Map"
    bl_description = ("Set up the compositor Depth Map pipeline. Creates a Suzanne preview "
                      "mesh and orthographic camera if absent, loads the compositor node group, "
                      "and wires the Z-pass into the depth group")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene      = context.scene
        view_layer = context.view_layer

        prev_sel    = list(bpy.context.selected_objects)
        prev_active = bpy.context.view_layer.objects.active

        bpy.ops.object.select_all(action='DESELECT')

        # Suzanne preview mesh
        if bpy.data.objects.get("Suzanne_Auto") is None:
            try:
                bpy.ops.mesh.primitive_monkey_add(size=2)
                obj = context.object
                obj.name = "Suzanne_Auto"
                obj.rotation_euler[0] = -math.pi / 2
                bpy.ops.object.modifier_add(type='SUBSURF')
                try:
                    obj.modifiers[-1].levels = 2
                    bpy.ops.object.modifier_apply(modifier=obj.modifiers[-1].name)
                except Exception:
                    pass
                try:
                    bpy.ops.object.shade_smooth()
                except Exception:
                    pass
            except Exception:
                pass

        # Camera
        cam = bpy.data.objects.get("Camera_Auto")
        if cam is None:
            try:
                bpy.ops.object.camera_add()
                cam = context.object
                cam.name = "Camera_Auto"
            except Exception:
                cam = None
        if cam is not None:
            cam.location       = (0.0, 0.0, 3.2229)
            cam.rotation_euler = (0.0, 0.0, 0.0)
            cam.scale          = (1.0, 1.0, 1.0)
            try:
                cam.data.type        = 'ORTHO'
                cam.data.ortho_scale = 2.9
                cam.data.clip_start  = 1e-6
                cam.data.clip_end    = 3.7
            except Exception:
                pass
            scene.camera = cam

        # Restore selection
        bpy.ops.object.select_all(action='DESELECT')
        for o in prev_sel:
            if o.name in bpy.data.objects:
                try:
                    bpy.data.objects[o.name].select_set(True)
                except Exception:
                    pass
        if prev_active and getattr(prev_active, "name", None) in bpy.data.objects:
            try:
                view_layer.objects.active = bpy.data.objects[prev_active.name]
            except Exception:
                pass

        # Render defaults
        scene.render.resolution_x = 500
        scene.render.resolution_y = 500
        scene.render.image_settings.color_depth  = '16'
        scene.render.image_settings.compression  = 0
        try:
            next(iter(scene.view_layers)).use_pass_z = True
        except Exception:
            pass

        # Load asset
        asset_path = find_asset_path()
        if asset_path is None:
            self.report({'ERROR'}, f"Depth compositor asset not found: {ASSET_BLEND_NAME}")
            return {'CANCELLED'}

        if NODE_GROUP_NAME not in bpy.data.node_groups:
            try:
                with bpy.data.libraries.load(asset_path, link=False) as (src, dst):
                    if NODE_GROUP_NAME not in src.node_groups:
                        self.report({'ERROR'}, "Depth compositor node group not found in asset file")
                        return {'CANCELLED'}
                    dst.node_groups = [NODE_GROUP_NAME]
            except Exception as e:
                self.report({'ERROR'}, f"Failed to load asset: {e}")
                return {'CANCELLED'}

        depth_group = bpy.data.node_groups.get(NODE_GROUP_NAME)
        if not depth_group:
            self.report({'ERROR'}, "Failed to access loaded depth node group")
            return {'CANCELLED'}

        try:
            force_all_rlayers_to_scene(scene, view_layer)
        except Exception:
            pass

        scene.use_nodes = True

        # Blender 5.0: node_tree may not be created automatically by use_nodes
        tree = get_compositor_tree(scene)
        if tree is None:
            # Try to force-create via the compositor attribute (Blender 4.3+/5.0)
            try:
                scene.compositor.node_tree  # access may auto-create
                tree = get_compositor_tree(scene)
            except Exception:
                pass
        if tree is None:
            # Last resort: create a node group and assign it
            try:
                ng = bpy.data.node_groups.new("Compositor", 'CompositorNodeTree')
                scene.node_tree = ng
                tree = ng
            except Exception:
                pass
        if tree is None:
            self.report({'INFO'}, "Asset loaded. Open Compositing editor and add 'Depth_Map_Comp_GN'.")
            scene.bas_relief_depth_map_created = True
            try:
                _apply_display_defaults_to_scene(scene)
            except Exception:
                pass
            return {'FINISHED'}

        try:
            tree.nodes.clear()
        except Exception:
            pass

        created = {}
        try:
            rl = tree.nodes.new("CompositorNodeRLayers")
            rl.location = (0, 0)
            try:
                rl.scene = scene
                rl.layer = (getattr(view_layer, "name", None)
                            or next(iter(scene.view_layers)).name)
            except Exception:
                pass
            created['rl'] = rl
        except Exception as e:
            self.report({'WARNING'}, f"Could not create Render Layers node: {e}")

        try:
            grp = tree.nodes.new("CompositorNodeGroup")
            grp.node_tree = depth_group
            grp.location  = (300, 0)
            created['grp'] = grp
        except Exception as e:
            self.report({'ERROR'}, f"Could not create Group node: {e}")
            return {'CANCELLED'}

        if 'rl' in created and 'grp' in created:
            try:
                tree.links.new(created['rl'].outputs["Depth"], created['grp'].inputs[0])
            except Exception:
                try:
                    tree.links.new(created['rl'].outputs[0], created['grp'].inputs[0])
                except Exception:
                    pass

        # Switch an area to the Compositor and show the scene node tree.
        # We store the area reference and do the actual switch via a timer
        # so Blender has finished drawing the current frame first (more reliable
        # in Blender 4.2+ where space properties can be read-only mid-operator).
        try:
            wm  = context.window_manager
            target_area = None

            # 1. prefer an existing Node Editor
            for window in wm.windows:
                for area in window.screen.areas:
                    if area.type == 'NODE_EDITOR':
                        target_area = area
                        break
                if target_area:
                    break

            # 2. fall back to largest non-essential area
            if target_area is None:
                for window in wm.windows:
                    for area in window.screen.areas:
                        if area.type not in ('VIEW_3D', 'PROPERTIES', 'OUTLINER'):
                            if (target_area is None or
                                    area.width * area.height > target_area.width * target_area.height):
                                target_area = area

            if target_area is not None:
                _area_ref  = target_area
                _scene_name = scene.name

                def _open_compositor():
                    try:
                        sc = bpy.data.scenes.get(_scene_name)
                        if sc is None:
                            return None
                        _area_ref.type = 'NODE_EDITOR'
                        space = _area_ref.spaces.active
                        space.tree_type = 'CompositorNodeTree'
                        # Correct API: assign node_tree directly
                        if sc.use_nodes and sc.node_tree:
                            space.node_tree = sc.node_tree
                        _area_ref.tag_redraw()
                    except Exception:
                        try:
                            _area_ref.tag_redraw()
                        except Exception:
                            pass
                    return None  # stop timer

                bpy.app.timers.register(_open_compositor, first_interval=0.1)
        except Exception:
            pass

        try:
            _apply_display_defaults_to_scene(scene)
        except Exception:
            pass

        scene.bas_relief_depth_map_created = True
        self.report({'INFO'}, "Depth Map pipeline ready — Compositor opened")
        return {'FINISHED'}


# -------------------------------------------------------------------------
# Register
# -------------------------------------------------------------------------
classes = (
    BASRELIEF_PT_main,
    BASRELIEF_OT_import_image,
    BASRELIEF_OT_run_bas_relief,
    BASRELIEF_OT_create_texture,
    BASRELIEF_OT_open_texture_image,
    BASRELIEF_OT_create_depth_map,
    BASRELIEF_OT_delete_depth_map,
    BASRELIEF_OT_save_render_image,
    BASRELIEF_OT_adjust_displace,
    BASRELIEF_PT_displace_adjust,
    BASRELIEF_PT_height_map_render,
)


def register():
    global _icons
    try:
        _icons = bpy.utils.previews.new()
    except Exception:
        _icons = None

    if not hasattr(bpy.types.Scene, SHARED_IMAGE_PROP):
        setattr(bpy.types.Scene, SHARED_IMAGE_PROP, bpy.props.StringProperty(
            name="Image Path",
            description="Shared image path used by Bas Relief and Height Map Generator",
            default="",
            subtype='FILE_PATH',
        ))

    if not hasattr(bpy.types.Scene, 'bas_relief_depth_map_created'):
        bpy.types.Scene.bas_relief_depth_map_created = bpy.props.BoolProperty(
            default=False)

    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            pass  # already registered — safe to skip

    try:
        for sc in bpy.data.scenes:
            _apply_display_defaults_to_scene(sc)
    except Exception:
        pass

    try:
        icon_path = os.path.join(os.path.dirname(__file__), 'icons', '09a861f687e07db4c9056c9499f6713f2.png')
        if _icons is not None and os.path.exists(icon_path):
            _icons.load('09a861f687e07db4c9056c9499f6713f2.png', icon_path, "IMAGE")
    except Exception:
        pass


def unregister():
    global _icons
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    try:
        if _icons is not None:
            bpy.utils.previews.remove(_icons)
    except Exception:
        pass
    try:
        if hasattr(bpy.types.Scene, SHARED_IMAGE_PROP):
            delattr(bpy.types.Scene, SHARED_IMAGE_PROP)
    except Exception:
        pass
    try:
        if hasattr(bpy.types.Scene, 'bas_relief_depth_map_created'):
            del bpy.types.Scene.bas_relief_depth_map_created
    except Exception:
        pass
    try:
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon
        for km, kmi in addon_keymaps.values():
            try:
                km.keymap_items.remove(kmi)
            except Exception:
                pass
    except Exception:
        pass
    addon_keymaps.clear()
