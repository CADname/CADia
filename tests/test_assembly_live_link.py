from pathlib import Path
import pytest

from standalonecad.core.engine import CadEngine


def _active_session_id(engine):
    return next(x['id'] for x in engine.document_sessions() if x['active'])


def test_open_part_edits_propagate_live_to_open_assembly_without_resave(tmp_path):
    e = CadEngine()
    e.execute('create_box', {
        'length_mm': 20, 'width_mm': 20, 'height_mm': 10,
        'replace': True, 'name': 'LinkedPart'
    })
    part_path = tmp_path / 'linked_part.scad.json'
    e.execute('save_document', {'path': str(part_path)})
    part_session = _active_session_id(e)
    original_volume = float(e.doc.shape.Volume())

    e.execute('new_assembly', {})
    assembly_session = _active_session_id(e)
    occ_name = e.execute('place_occurrence', {
        'path': str(part_path), 'grounded': True,
        'position_mm': [0, 0, 0], 'rotation_deg_xyz': [0, 0, 0]
    })['occurrence_name']
    assert e.doc.occurrences[occ_name].source_session_id == part_session
    assert float(e.doc.shape.Volume()) == pytest.approx(original_volume)

    # Edit the already-open part but deliberately do NOT save it again.  The assembly
    # must still display/use the new in-memory B-Rep instead of the placement snapshot.
    e.activate_document(part_session)
    e.execute('create_cylinder', {
        'diameter_mm': 6, 'height_mm': 10,
        'origin_mm': [10, 10, 0], 'operation': 'cut',
        'replace': False, 'name': 'LiveHole'
    })
    edited_volume = float(e.doc.shape.Volume())
    assert edited_volume < original_volume

    e.activate_document(assembly_session)
    occ = e.doc.occurrences[occ_name]
    assert float(occ.shape.Volume()) == pytest.approx(edited_volume)
    assert float(e.doc.shape.Volume()) == pytest.approx(edited_volume)

    # Disk file is intentionally stale: proving the assembly followed the open source
    # document rather than silently reloading the old placement file.
    disk_engine = CadEngine()
    disk_engine.execute('open_document', {'path': str(part_path)})
    assert float(disk_engine.doc.shape.Volume()) == pytest.approx(original_volume)


def test_multiple_open_assemblies_follow_same_source_part(tmp_path):
    e = CadEngine()
    e.execute('create_box', {'length_mm': 12, 'width_mm': 10, 'height_mm': 8, 'replace': True})
    part_path = tmp_path / 'shared.scad.json'
    e.execute('save_document', {'path': str(part_path)})
    part_session = _active_session_id(e)

    assembly_sessions = []
    occurrence_names = []
    for _ in range(2):
        e.execute('new_assembly', {})
        assembly_sessions.append(_active_session_id(e))
        occurrence_names.append(e.execute('place_occurrence', {'path': str(part_path), 'grounded': True})['occurrence_name'])

    e.activate_document(part_session)
    e.execute('create_cylinder', {
        'diameter_mm': 4, 'height_mm': 8,
        'origin_mm': [6, 5, 0], 'operation': 'cut', 'replace': False
    })
    target_volume = float(e.doc.shape.Volume())

    for sid, name in zip(assembly_sessions, occurrence_names):
        e.activate_document(sid)
        assert float(e.doc.occurrences[name].shape.Volume()) == pytest.approx(target_volume)
        assert float(e.doc.shape.Volume()) == pytest.approx(target_volume)


def test_live_link_runtime_id_is_not_persisted(tmp_path):
    e = CadEngine()
    e.execute('create_box', {'length_mm': 10, 'width_mm': 10, 'height_mm': 5, 'replace': True})
    part_path = tmp_path / 'part.scad.json'
    e.execute('save_document', {'path': str(part_path)})
    e.execute('new_assembly', {})
    name = e.execute('place_occurrence', {'path': str(part_path)})['occurrence_name']
    assert e.doc.occurrences[name].source_session_id is not None
    payload = e.doc.serialize()
    assert 'source_session_id' not in payload['occurrences'][name]
