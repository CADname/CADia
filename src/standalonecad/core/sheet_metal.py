from __future__ import annotations

"""Isolated deterministic sheet-metal subsystem.

The normal part/feature engine never calls this module. Flat-pattern export is backed
by explicit sheet-metal design intent rather than a planar-face heuristic.

Scope intentionally implemented here:
- one rectangular base sheet
- zero or more straight edge flanges on the four base edges
- explicit thickness, bend radius and K-factor metadata
- deterministic folded sharp-fold B-Rep for visualization/STEP
- neutral-axis developed flat pattern with bend lines for DXF

This is not a claim of Autodesk Sheet Metal binary/feature parity.  Unsupported
multi-level hems, lofted flanges, corner relief and non-rectangular bases remain
truthful errors instead of guessed geometry.
"""

import math
from typing import Any
import cadquery as cq

EDGES={"top","bottom","left","right"}


def validate_rule(thickness_mm:float,bend_radius_mm:float,k_factor:float):
    t=float(thickness_mm); r=float(bend_radius_mm); k=float(k_factor)
    if not math.isfinite(t) or t<=0:raise ValueError('sheet metal thickness_mm must be > 0')
    if not math.isfinite(r) or r<0:raise ValueError('sheet metal bend_radius_mm must be >= 0')
    if not math.isfinite(k) or not (0.0<=k<=1.0):raise ValueError('sheet metal k_factor must be between 0 and 1')
    return t,r,k


def bend_allowance(angle_deg:float,radius_mm:float,thickness_mm:float,k_factor:float)->float:
    return math.radians(abs(float(angle_deg)))*(float(radius_mm)+float(k_factor)*float(thickness_mm))


def make_base(width_mm:float,height_mm:float,thickness_mm:float):
    w=float(width_mm); h=float(height_mm); t=float(thickness_mm)
    if w<=0 or h<=0 or t<=0:raise ValueError('sheet metal base dimensions must be > 0')
    return cq.Solid.makeBox(w,h,t,(-w/2.0,-h/2.0,0.0))


def make_flange(base_width_mm:float,base_height_mm:float,thickness_mm:float,edge:str,length_mm:float,angle_deg:float):
    """Create one straight flange panel as a robust one-solid sharp fold.

    Two thickness-side placements are tried only inside this isolated sheet-metal path.
    The candidate that actually fuses to the rectangular base as one solid is selected.
    This handles both positive and negative fold directions without changing the normal
    part/feature engine or pretending that a disconnected panel is a successful flange.
    """
    w=float(base_width_mm); h=float(base_height_mm); t=float(thickness_mm); L=float(length_mm); a=float(angle_deg)
    edge=str(edge).lower()
    if edge not in EDGES:raise ValueError('sheet metal edge must be top|bottom|left|right')
    if L<=0:raise ValueError('sheet metal flange length_mm must be > 0')
    if not (0.0 < abs(a) < 180.0):raise ValueError('sheet metal flange angle_deg must be between -180 and 180 and non-zero')

    def candidate(z0:float):
        if edge=='top':
            panel=cq.Solid.makeBox(w,L,t,(-w/2.0,h/2.0,z0))
            return panel.rotate((-w/2.0,h/2.0,0.0),(w/2.0,h/2.0,0.0),a)
        if edge=='bottom':
            panel=cq.Solid.makeBox(w,L,t,(-w/2.0,-h/2.0-L,z0))
            return panel.rotate((-w/2.0,-h/2.0,0.0),(w/2.0,-h/2.0,0.0),-a)
        if edge=='right':
            panel=cq.Solid.makeBox(L,h,t,(w/2.0,-h/2.0,z0))
            return panel.rotate((w/2.0,-h/2.0,0.0),(w/2.0,h/2.0,0.0),-a)
        panel=cq.Solid.makeBox(L,h,t,(-w/2.0-L,-h/2.0,z0))
        return panel.rotate((-w/2.0,-h/2.0,0.0),(-w/2.0,h/2.0,0.0),a)

    base=make_base(w,h,t)
    valid=[]
    for z0 in (0.0,-t):
        panel=candidate(z0)
        try:
            fused=base.fuse(panel)
            solids=len(fused.Solids())
            if fused.isValid() and solids==1:
                # Prefer the placement with the smallest material overlap: it is the
                # sharp-fold analogue of choosing the correct inside thickness side.
                overlap=max(0.0,base.Volume()+panel.Volume()-fused.Volume())
                valid.append((overlap,panel))
        except Exception:
            continue
    if not valid:
        raise ValueError('sheet metal flange could not form one valid connected solid for this angle')
    valid.sort(key=lambda x:x[0])
    return valid[0][1]


def flat_pattern_sketch(sheet:dict[str,Any]):
    base=sheet.get('base') or {}; rule=sheet.get('rule') or {}
    w=float(base['width_mm']); h=float(base['height_mm']); t=float(rule['thickness_mm']); default_r=float(rule['bend_radius_mm']); k=float(rule['k_factor'])
    sk=cq.Sketch().rect(w,h)
    # Separate rectangles are intentional: DXF contains explicit panel boundaries and
    # bend lines, while the developed envelope follows neutral-axis bend allowance.
    for flange in sheet.get('flanges') or []:
        edge=str(flange['edge']).lower(); L=float(flange['length_mm']); angle=float(flange['angle_deg']); r=float(flange.get('bend_radius_mm',default_r)); ba=bend_allowance(angle,r,t,k); dev=L+ba
        if edge=='top':
            sk=sk.push([(0.0,h/2.0+dev/2.0)]).rect(w,dev).reset().segment((-w/2.0,h/2.0),(w/2.0,h/2.0))
        elif edge=='bottom':
            sk=sk.push([(0.0,-h/2.0-dev/2.0)]).rect(w,dev).reset().segment((-w/2.0,-h/2.0),(w/2.0,-h/2.0))
        elif edge=='right':
            sk=sk.push([(w/2.0+dev/2.0,0.0)]).rect(dev,h).reset().segment((w/2.0,-h/2.0),(w/2.0,h/2.0))
        elif edge=='left':
            sk=sk.push([(-w/2.0-dev/2.0,0.0)]).rect(dev,h).reset().segment((-w/2.0,-h/2.0),(-w/2.0,h/2.0))
        else:raise ValueError('invalid stored sheet metal edge: '+edge)
    return sk


def flat_pattern_metadata(sheet:dict[str,Any]):
    rule=sheet.get('rule') or {}; base=sheet.get('base') or {}; t=float(rule['thickness_mm']); k=float(rule['k_factor']); default_r=float(rule['bend_radius_mm'])
    bends=[]
    for f in sheet.get('flanges') or []:
        r=float(f.get('bend_radius_mm',default_r)); bends.append({**dict(f),'bend_allowance_mm':bend_allowance(float(f['angle_deg']),r,t,k)})
    return {'base':dict(base),'rule':dict(rule),'bends':bends,'method':'neutral_axis_k_factor','folded_brep':'sharp-fold deterministic panels'}
