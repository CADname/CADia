from pathlib import Path


def _ui_source():
    return Path('src/standalonecad/ui/qt_app.py').read_text(encoding='utf-8')


def test_assembly_mesh_is_built_per_occurrence_with_explicit_owner():
    ui=_ui_source()
    assert 'for occurrence_name, occurrence_shape in assembly_shapes:' in ui
    assert 'faces=list(occurrence_shape.Faces())' in ui
    assert 'frecs=face_records(occurrence_shape)' in ui
    assert 'edges=list(occurrence_shape.Edges())' in ui
    assert 'erecs=edge_records(occurrence_shape)' in ui
    assert 'tagged["occurrence_name"]=str(occurrence_name)' in ui
    # The old fragile compound -> occurrence reverse matching must not return.
    assert 'face_owner_by_hash' not in ui
    assert 'edge_owner_by_hash' not in ui
    assert 'face.isSame(candidate)' not in ui
    assert 'edge.isSame(candidate)' not in ui


def test_actor_occurrence_map_uses_explicit_mesh_owner_only():
    ui=_ui_source()
    assert 'owner=rec.get("occurrence_name")' in ui
    assert 'self._actor_occurrence[actor]=str(owner)' in ui
    assert '_occurrence_face_ids' not in ui
    assert '_occurrence_edge_ids' not in ui


def test_transparency_toggle_matches_part_style_for_assembly():
    ui=_ui_source()
    assert 'self.transparency_btn=QPushButton("Transparency")' in ui
    assert 'self.transparency_btn.setText("Disable transparency" if selected else "Transparency")' in ui
    # Assembly must behave exactly like Part at the document level: no occurrence selection required.
    assert 'target="__PART__" if doc.doc_type=="part" else "__ASSEMBLY__"' in ui
    assert 'doc.doc_type=="assembly" and "__ASSEMBLY__" in targets' in ui
    assert 'elif doc is not None and doc.doc_type=="assembly":selected="__ASSEMBLY__" in targets' in ui
    assert 'Select a component from the Occurrences tree or 3D view before changing transparency.' not in ui
    assert 'if target in targets:targets.remove(target)' in ui
    assert 'else:targets.add(target)' in ui
    assert 'for actor in self.actor_face:' in ui
    assert 'for actor in self.actor_edge:' in ui
    assert 'actor.GetProperty().SetOpacity(0.25)' in ui
    assert 'actor.GetProperty().SetOpacity(1.0)' in ui


def test_assembly_document_topology_snapshot_is_preserved_separately():
    ui=_ui_source()
    assert 'payload["face_topology"]=face_records(shape)' in ui
    assert 'payload["edge_topology"]=edge_records(shape)' in ui
    assert 'assembly_shapes.append((str(name),transform_shape(occ.shape,occ.position_mm,occ.rotation_deg_xyz)))' in ui
