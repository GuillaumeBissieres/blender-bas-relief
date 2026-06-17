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
# Dependency management for Bas Relief — Height Map Generator
#
# Third-party libraries used (all installed via pip from PyPI):
#   - numpy        : BSD License
#   - opencv-python: Apache 2.0 License
#   - Pillow       : HPND License (MIT-compatible)
#   - torch        : BSD 3-Clause License (Meta Platforms)
#   - torchvision  : BSD 3-Clause License (Meta Platforms)
#
# MiDaS model weights (Intel ISL) — MIT License
#   Downloaded at runtime via torch.hub — not bundled in this add-on.
#
# This add-on does NOT redistribute any of these libraries.
# It only instructs pip to download and install them from PyPI,
# which is equivalent to the user doing so manually.
# -----------------------------------------------------------------------

import bpy
import subprocess
import sys
import importlib
import os

# (package_import_name, pip_install_name, version_hint)
REQUIRED_PACKAGES = [
    ("numpy",    "numpy",          ""),
    ("cv2",      "opencv-python",  ""),
    ("PIL",      "Pillow",         ""),
    ("torch",    "torch",          ""),
    ("torchvision", "torchvision", ""),
]


def _check_package(import_name):
    """Return True if the package can be imported."""
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def get_status():
    """Return a list of (pip_name, is_installed) for all required packages."""
    return [(pip, _check_package(imp)) for imp, pip, _ in REQUIRED_PACKAGES]


def all_installed():
    return all(ok for _, ok in get_status())


def install_package(pip_name, report_fn=None):
    """Install a single package into Blender's Python using pip."""
    python = sys.executable
    cmd = [python, "-m", "pip", "install", "--upgrade", pip_name]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            if report_fn:
                report_fn(f"✓ {pip_name} installed successfully")
            return True
        else:
            if report_fn:
                report_fn(f"✗ {pip_name} failed: {result.stderr[-200:]}")
            return False
    except subprocess.TimeoutExpired:
        if report_fn:
            report_fn(f"✗ {pip_name} timed out after 5 minutes")
        return False
    except Exception as e:
        if report_fn:
            report_fn(f"✗ {pip_name} error: {e}")
        return False


# -----------------------------------------------------------------------
# Operators
# -----------------------------------------------------------------------

class BASRELIEF_OT_InstallDependencies(bpy.types.Operator):
    bl_idname   = "basrelief.install_dependencies"
    bl_label    = "Install Dependencies"
    bl_description = (
        "Download and install the required Python libraries for the Height Map "
        "Generator (numpy, opencv-python, Pillow, torch, torchvision). "
        "Requires an internet connection. torch is ~800 MB — this may take "
        "several minutes. Blender may appear frozen during installation."
    )
    bl_options  = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        messages = []

        def report_fn(msg):
            messages.append(msg)
            print(f"[Bas Relief] {msg}")

        self.report({'INFO'}, "Installing dependencies… check the system console for progress.")

        failed = []
        for imp_name, pip_name, _ in REQUIRED_PACKAGES:
            if _check_package(imp_name):
                report_fn(f"✓ {pip_name} already installed — skipped")
                continue
            ok = install_package(pip_name, report_fn)
            if not ok:
                failed.append(pip_name)

        if failed:
            self.report(
                {'ERROR'},
                f"Some packages failed to install: {', '.join(failed)}. "
                "Check the system console for details."
            )
        else:
            self.report(
                {'INFO'},
                "All dependencies installed successfully! "
                "Restart Blender to activate the Height Map Generator."
            )
        return {'FINISHED'}


class BASRELIEF_OT_CheckDependencies(bpy.types.Operator):
    bl_idname   = "basrelief.check_dependencies"
    bl_label    = "Check Status"
    bl_description = "Check which required libraries are currently installed"
    bl_options  = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        status = get_status()
        lines = []
        for pip_name, ok in status:
            lines.append(f"{'✓' if ok else '✗'} {pip_name}")
        msg = " | ".join(lines)
        level = 'INFO' if all(ok for _, ok in status) else 'WARNING'
        self.report({level}, msg)
        return {'FINISHED'}


# -----------------------------------------------------------------------
# Addon Preferences
# -----------------------------------------------------------------------

class BAS_RELIEF_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        layout = self.layout
        status = get_status()
        all_ok = all(ok for _, ok in status)

        # Header
        box = layout.box()
        row = box.row()
        row.label(
            text="Height Map Generator — Required Libraries",
            icon='SCRIPT'
        )

        # Status per package
        grid = box.column(align=True)
        for pip_name, ok in status:
            row = grid.row()
            row.label(
                text=pip_name,
                icon='CHECKMARK' if ok else 'X'
            )
            if ok:
                row.label(text="Installed", icon='BLANK1')
            else:
                row.label(text="Not found", icon='BLANK1')

        layout.separator()

        if not all_ok:
            # Warning box
            warn = layout.box()
            warn.label(
                text="Some libraries are missing. The Height Map Generator",
                icon='ERROR'
            )
            warn.label(text="will not work until all dependencies are installed.")
            warn.label(text="torch (~800 MB) requires a stable internet connection.")
            layout.separator()

            # Install button
            col = layout.column()
            col.scale_y = 1.5
            col.operator(
                "basrelief.install_dependencies",
                text="Install All Dependencies",
                icon='IMPORT'
            )
        else:
            layout.label(
                text="All dependencies are installed. Height Map Generator is ready.",
                icon='CHECKMARK'
            )

        layout.separator()
        row = layout.row()
        row.operator("basrelief.check_dependencies", text="Refresh Status", icon='FILE_REFRESH')

        layout.separator()
        layout.label(text="Third-party licenses:", icon='INFO')
        layout.label(text="numpy (BSD) • opencv-python (Apache 2.0) • Pillow (HPND)")
        layout.label(text="torch & torchvision (BSD 3-Clause, Meta Platforms)")
        layout.label(text="MiDaS model weights (MIT, Intel ISL) — downloaded at runtime")


# -----------------------------------------------------------------------
# Register
# -----------------------------------------------------------------------

classes = (
    BASRELIEF_OT_InstallDependencies,
    BASRELIEF_OT_CheckDependencies,
    BAS_RELIEF_Preferences,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
