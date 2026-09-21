from __future__ import annotations

import time
import cadquery as cq

from standalonecad.core.document import CadDocument, Parameter
from standalonecad.core.engine import CadEngine
from standalonecad.core.gears import involute_spur_gear
from standalonecad.core.topology import edge_records, face_records


def _bruteforce_adjacent_face_geoms(shape, edge):
    out=[]
    for face in shape.Faces():
        if any(fe.isSame(edge) for fe in face.Edges()):
            out.append(str(face.geomType()))
    return sorted(out)


def test_set_units_accepts_upstream_ft_contract():
    e=CadEngine()
    r=e.execute('set_units', {'length_unit':'ft'})
    assert r['ok'] is True
    assert r['length_unit']=='ft'
    assert e.doc.units=='ft'


def test_parameter_cache_is_semantic_and_self_invalidating():
    d=CadDocument()
    d.parameters['a']=Parameter('a','10','mm','user')
    d.parameters['b']=Parameter('b','a*2','mm','user')
    assert d.numeric_params()=={'a':10.0,'b':20.0}
    first_cache=d._numeric_param_cache_vals
    assert d.numeric_params()=={'a':10.0,'b':20.0}
    assert d._numeric_param_cache_vals is first_cache
    # Parameter objects are public/mutable in the compatibility layer. The cache key is
    # content-derived, so even a direct edit must invalidate safely.
    d.parameters['a'].expression='12'
    assert d.numeric_params()=={'a':12.0,'b':24.0}
    assert d._numeric_param_cache_vals is not first_cache


def test_topology_adjacency_matches_bruteforce_and_records_are_cached():
    shape=(cq.Workplane('XY').box(30,20,10).faces('>Z').workplane().hole(6).val())
    recs=edge_records(shape)
    edges=list(shape.Edges())
    assert len(recs)==len(edges)
    for rec,edge in zip(recs,edges):
        assert rec['adjacent_face_geoms']==_bruteforce_adjacent_face_geoms(shape,edge)
        assert rec['adjacent_face_count']==len(rec['adjacent_face_geoms'])
    # The second query should use the body-attached topology cache, not enumerate again.
    assert edge_records(shape) is recs
    frecs=face_records(shape)
    assert face_records(shape) is frecs


def test_complex_gear_edge_topology_no_longer_uses_quadratic_face_scan():
    shape,_=involute_spur_gear(2,24,10,8,20,0)
    assert len(shape.Edges())>1000
    t0=time.perf_counter()
    recs=edge_records(shape)
    elapsed=time.perf_counter()-t0
    assert len(recs)==len(shape.Edges())
    # v9.2's nested edge->face->face-edge scan took >25 s on this exact 24T body
    # in the release environment. Keep a generous guard for slower machines while
    # still catching an accidental return to that algorithm.
    assert elapsed < 8.0, f'24T topology indexing regressed: {elapsed:.3f}s'


def test_feature_edit_reuses_unchanged_prefix_and_matches_full_rebuild():
    d=CadDocument()
    d.add_feature('box','Base',{'length_mm':10,'width_mm':10,'height_mm':10,'origin_mm':[0,0,0]},'new')
    d.add_feature('box','Boss',{'length_mm':8,'width_mm':10,'height_mm':10,'origin_mm':[8,0,0]},'join')
    d.add_feature('box','Boss2',{'length_mm':4,'width_mm':10,'height_mm':10,'origin_mm':[14,0,0]},'join')
    prefix=d._feature_shape_cache[0]
    d.edit_feature('Boss',{'length_mm':10})
    assert d._feature_shape_cache[0] is prefix
    inc=(d.shape.Volume(), d.shape.BoundingBox().xlen, len(d.shape.Faces()), len(d.shape.Edges()))
    # A forced full rebuild must produce the same B-Rep metrics.
    d.rebuild(0)
    full=(d.shape.Volume(), d.shape.BoundingBox().xlen, len(d.shape.Faces()), len(d.shape.Edges()))
    assert inc==full


def test_delete_and_suppress_incremental_history_match_full_rebuild():
    d=CadDocument()
    d.add_feature('box','A',{'length_mm':10,'width_mm':10,'height_mm':10,'origin_mm':[0,0,0]},'new')
    d.add_feature('box','B',{'length_mm':6,'width_mm':10,'height_mm':10,'origin_mm':[8,0,0]},'join')
    d.add_feature('box','C',{'length_mm':6,'width_mm':10,'height_mm':10,'origin_mm':[12,0,0]},'join')
    d.suppress_feature('B',True)
    inc1=(d.shape.Volume(),d.shape.BoundingBox().xlen)
    d.rebuild(0)
    assert inc1==(d.shape.Volume(),d.shape.BoundingBox().xlen)
    d.delete_feature('B')
    inc2=(d.shape.Volume(),d.shape.BoundingBox().xlen)
    d.rebuild(0)
    assert inc2==(d.shape.Volume(),d.shape.BoundingBox().xlen)


def test_incremental_edit_replays_downstream_persistent_edge_feature():
    e=CadEngine()
    e.execute('create_box',{'length_mm':20,'width_mm':10,'height_mm':8,'replace':True,'name':'Base'})
    e.execute('create_box',{'length_mm':10,'width_mm':10,'height_mm':4,'origin_mm':[5,0,8],'operation':'join','name':'Boss'})
    edge=next(r for r in e.doc.topology()['edges'] if r['geom']=='LINE' and abs(r['center'][2]-12.0)<1e-6)
    e.execute('fillet',{'edge_ids':[edge['id']],'radius_mm':1.0})
    e.execute('edit_feature',{'feature_name':'Boss','updates':{'length_mm':12}})
    inc=(e.doc.shape.Volume(),e.doc.shape.BoundingBox().xlen,len(e.doc.shape.Faces()),len(e.doc.shape.Edges()))
    assert e.doc.features[-1].kind=='fillet'
    e.doc.rebuild(0)
    full=(e.doc.shape.Volume(),e.doc.shape.BoundingBox().xlen,len(e.doc.shape.Faces()),len(e.doc.shape.Edges()))
    assert inc==full


def test_public_topology_result_cannot_mutate_internal_cache():
    d=CadDocument()
    d.add_feature('box','Base',{'length_mm':10,'width_mm':10,'height_mm':10,'origin_mm':[0,0,0]},'new')
    a=d.topology()
    original=a['edges'][0]['id']
    a['edges'][0]['id']='CORRUPTED_BY_CALLER'
    b=d.topology()
    assert b['edges'][0]['id']==original
