"""
Wall Splitter Func Node — Extract each IfcWall from an IFC file into its own IFC file.

Input : str   — path to a source IFC file, wow, yeah
Output: list[str] — paths to the generated per-wall IFC files

Semantic Layers : PHYSICAL, SPATIAL, RELATIONSHIP
Supported IFC   : IFC2X3, IFC4, IFC4X3
Library         : ifcopenshell
"""

from typing import Set, List, Dict, Optional, Any
from enum import Enum
from pathlib import Path
import ifcopenshell
import ifcopenshell.util.element


class IFCVersion(Enum):
    IFC2X3 = "IFC2X3"
    IFC4 = "IFC4"
    IFC4X3 = "IFC4X3"


class IFCSemanticLayer(Enum):
    SPATIAL = "spatial"
    PHYSICAL = "physical"
    LOGICAL = "logical"
    TYPE = "type"
    RELATIONSHIP = "relationship"
    PROPERTY = "property"
    GEOMETRY = "geometry"
    MATERIAL = "material"


class WallSplitterNode:
    """
    Input:  file path  (str)
    Output: file paths (list[str])

    Each output IFC file contains one IfcWall with its full context:
    project hierarchy, geometry, type, properties, material, and relationships.
    """

    SUPPORTED_VERSIONS: Set[IFCVersion] = {
        IFCVersion.IFC2X3, IFCVersion.IFC4, IFCVersion.IFC4X3
    }
    SEMANTIC_LAYERS: Set[IFCSemanticLayer] = {
        IFCSemanticLayer.PHYSICAL,
        IFCSemanticLayer.SPATIAL,
        IFCSemanticLayer.RELATIONSHIP,
    }

    def run(self, file_path: str) -> List[str]:
        """
        Split every IfcWall in *file_path* into a separate IFC file.

        Output directory is created next to the source file:
            <source_stem>_walls/wall_0000_<guid>.ifc

        Returns absolute paths of all generated files.
        """
        src = Path(file_path).resolve()
        ifc = ifcopenshell.open(str(src))
        self._validate_version(ifc)

        walls = self._get_walls(ifc)
        if not walls:
            return []

        out_dir = src.parent / f"{src.stem}_walls"
        out_dir.mkdir(parents=True, exist_ok=True)

        output_paths: List[str] = []

        for idx, wall in enumerate(walls):
            guid = getattr(wall, "GlobalId", "unknown")
            fname = f"wall_{idx:04d}_{guid}.ifc"
            dest = out_dir / fname

            new_ifc = self._extract_wall(ifc, wall)
            new_ifc.write(str(dest))
            output_paths.append(str(dest))

        print(f"[WallSplitterNode] {len(output_paths)} walls exported: {output_paths}")
        return output_paths

    # ============ Internal ============

    def _validate_version(self, ifc: ifcopenshell.file) -> None:
        version = self._detect_version(ifc.schema)
        if self.SUPPORTED_VERSIONS and version and version not in self.SUPPORTED_VERSIONS:
            raise ValueError(f"IFC version {ifc.schema} not supported")

    @staticmethod
    def _detect_version(schema: str) -> Optional[IFCVersion]:
        s = schema.upper()
        if "IFC2X3" in s:
            return IFCVersion.IFC2X3
        if "IFC4X3" in s:
            return IFCVersion.IFC4X3
        if "IFC4" in s:
            return IFCVersion.IFC4
        return None

    @staticmethod
    def _get_walls(ifc: ifcopenshell.file) -> List[Any]:
        """Return deduplicated wall occurrences (handles IFC2X3 IfcWallStandardCase)."""
        walls = list(ifc.by_type("IfcWall"))
        walls.extend(ifc.by_type("IfcWallStandardCase"))
        seen: set = set()
        unique: List[Any] = []
        for w in walls:
            if w.id() not in seen:
                seen.add(w.id())
                unique.append(w)
        return unique

    def _extract_wall(self, ifc: ifcopenshell.file, wall) -> ifcopenshell.file:
        """Create a minimal IFC file containing one wall with full context."""
        new_ifc = ifcopenshell.file(schema=ifc.schema)
        entity_map: Dict[int, Any] = {}

        self._copy_header(ifc, new_ifc)

        projects = ifc.by_type("IfcProject")
        if projects:
            self._deep_copy(projects[0], new_ifc, entity_map)

        self._deep_copy(wall, new_ifc, entity_map)

        wall_type = ifcopenshell.util.element.get_type(wall)
        if wall_type:
            self._deep_copy(wall_type, new_ifc, entity_map)

        container = ifcopenshell.util.element.get_container(wall)
        if container:
            self._deep_copy(container, new_ifc, entity_map)

        self._copy_relationships(ifc, wall, new_ifc, entity_map)

        return new_ifc

    @staticmethod
    def _copy_header(src: ifcopenshell.file, dst: ifcopenshell.file) -> None:
        try:
            if hasattr(src.header, "file_description"):
                dst.header.file_description.description = (
                    src.header.file_description.description
                )
            if hasattr(src.header, "file_name"):
                dst.header.file_name.name = src.header.file_name.name
        except Exception:
            pass

    def _deep_copy(
        self, entity, new_ifc: ifcopenshell.file, entity_map: Dict[int, Any]
    ) -> Any:
        eid = entity.id()
        if eid in entity_map:
            return entity_map[eid]
        if eid == 0:
            return entity

        new_attrs = []
        for i in range(len(entity)):
            new_attrs.append(self._resolve_attr(entity[i], new_ifc, entity_map))

        try:
            new_entity = new_ifc.create_entity(entity.is_a(), *new_attrs)
        except Exception:
            new_entity = new_ifc.add(entity)

        entity_map[eid] = new_entity
        return new_entity

    def _resolve_attr(self, value, new_ifc, entity_map):
        if value is None:
            return None
        if isinstance(value, ifcopenshell.entity_instance):
            return self._deep_copy(value, new_ifc, entity_map)
        if isinstance(value, (tuple, list)):
            resolved = [self._resolve_attr(v, new_ifc, entity_map) for v in value]
            return tuple(resolved) if isinstance(value, tuple) else resolved
        return value

    def _copy_relationships(
        self, ifc: ifcopenshell.file, wall, new_ifc: ifcopenshell.file,
        entity_map: Dict[int, Any],
    ) -> None:
        rels: set = set()

        for rel in ifc.by_type("IfcRelDefinesByProperties"):
            if wall in rel.RelatedObjects:
                rels.add(rel)
        for rel in ifc.by_type("IfcRelDefinesByType"):
            if wall in rel.RelatedObjects:
                rels.add(rel)
        for rel in ifc.by_type("IfcRelAssociatesMaterial"):
            if wall in rel.RelatedObjects:
                rels.add(rel)
        for rel in ifc.by_type("IfcRelContainedInSpatialStructure"):
            if wall in rel.RelatedElements:
                rels.add(rel)
        for rel in ifc.by_type("IfcRelVoidsElement"):
            if rel.RelatingBuildingElement == wall:
                rels.add(rel)
        for rel in ifc.by_type("IfcRelFillsElement"):
            try:
                void_rel = rel.RelatingOpeningElement.VoidsElements
                if void_rel and void_rel[0].RelatingBuildingElement == wall:
                    rels.add(rel)
            except Exception:
                pass

        for rel in rels:
            self._deep_copy(rel, new_ifc, entity_map)


# ============ CLI ============

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <input.ifc>", file=sys.stderr)
        sys.exit(1)

    node = WallSplitterNode()
    paths = node.run(sys.argv[1])

    for p in paths:
        print(p) 
