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

# NOTE: bl_info removed — blender_manifest.toml is the single source of
# metadata for Blender 4.2+ extensions.

from . import dependencies
from . import bas_relief_tools
from . import height_map_tools


def register():
    dependencies.register()
    bas_relief_tools.register()
    height_map_tools.register()


def unregister():
    height_map_tools.unregister()
    bas_relief_tools.unregister()
    dependencies.unregister()


if __name__ == "__main__":
    register()
