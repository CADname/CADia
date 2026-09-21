import { GizmoHelper, GizmoViewport, Grid, Line, OrbitControls } from '@react-three/drei'
import { Canvas, ThreeEvent, useThree } from '@react-three/fiber'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import type { CadMesh, EdgeMesh, FaceMesh, Selection } from '../types'

type Props = {
  mesh: CadMesh | null
  selection: Selection
  selectionMode: 'face' | 'edge' | 'object'
  showEdges: boolean
  showGrid: boolean
  transparent: boolean
  sectionAxis: 'off' | 'x' | 'y' | 'z'
  sectionOffset: number
  orientation: string
  theme: 'dark' | 'light'
  onSelect: (selection: Selection) => void
}

function occurrenceColor(name?: string | null): string {
  if (!name) return '#aeb9ca'
  let hash = 0
  for (let i = 0; i < name.length; i += 1) hash = name.charCodeAt(i) + ((hash << 5) - hash)
  const palette = ['#9fb7da', '#b8a8d4', '#92bdb5', '#d0ad8f', '#a7b4c8', '#c0a6ad']
  return palette[Math.abs(hash) % palette.length]
}

const Face = memo(function Face({ face, selected, selectionMode, transparent, clipPlane, onSelect }: {
  face: FaceMesh
  selected: boolean
  selectionMode: 'face' | 'edge' | 'object'
  transparent: boolean
  clipPlane: THREE.Plane | null
  onSelect: (selection: Selection) => void
}) {
  const geometry = useMemo(() => {
    const value = new THREE.BufferGeometry()
    value.setAttribute('position', new THREE.Float32BufferAttribute(face.positions, 3))
    value.setIndex(face.indices)
    value.computeVertexNormals()
    value.computeBoundingSphere()
    return value
  }, [face.indices, face.positions])
  useEffect(() => () => geometry.dispose(), [geometry])

  const click = (event: ThreeEvent<PointerEvent>) => {
    if (selectionMode === 'edge') return
    event.stopPropagation()
    if (selectionMode === 'object') {
      if (face.occurrence_name) {
        onSelect({ type: 'occurrence', occurrence_name: face.occurrence_name })
      } else {
        onSelect({ type: 'part' })
      }
      return
    }
    onSelect({ type: 'face', face_ref: face.id, occurrence_name: face.occurrence_name || undefined })
  }

  return (
    <mesh geometry={geometry} onPointerDown={click}>
      <meshStandardMaterial
        color={selected ? '#5b8cff' : occurrenceColor(face.occurrence_name)}
        emissive={selected ? '#173f97' : '#000000'}
        emissiveIntensity={selected ? 0.48 : 0}
        roughness={0.58}
        metalness={0.08}
        side={THREE.DoubleSide}
        transparent={transparent}
        opacity={transparent ? 0.28 : 1}
        depthWrite={!transparent}
        clippingPlanes={clipPlane ? [clipPlane] : []}
      />
    </mesh>
  )
})

const Edge = memo(function Edge({ edge, selected, hovered }: {
  edge: EdgeMesh
  selected: boolean
  hovered: boolean
}) {
  const points = useMemo(() => {
    const output: [number, number, number][] = []
    for (let index = 0; index < edge.positions.length; index += 3) {
      output.push([edge.positions[index], edge.positions[index + 1], edge.positions[index + 2]])
    }
    return output
  }, [edge.positions])

  return (
    <Line
      points={points}
      color={selected ? '#ffbb45' : hovered ? '#6fa7ff' : '#27394f'}
      lineWidth={selected ? 3.8 : hovered ? 2.6 : 1.15}
      transparent
      opacity={selected ? 1 : hovered ? 0.96 : 0.78}
      raycast={() => {}}
    />
  )
})

type ProjectedHit = {
  edge: EdgeMesh
  distancePx: number
  depth: number
}

function pointSegmentDistance(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const abx = bx - ax
  const aby = by - ay
  const denom = abx * abx + aby * aby
  if (denom <= 1e-12) {
    const dx = px - ax
    const dy = py - ay
    return { distance: Math.hypot(dx, dy), t: 0 }
  }
  let t = ((px - ax) * abx + (py - ay) * aby) / denom
  t = Math.max(0, Math.min(1, t))
  const qx = ax + abx * t
  const qy = ay + aby * t
  return { distance: Math.hypot(px - qx, py - qy), t }
}

/**
 * Browser equivalent of the desktop vtkCellPicker edge path.
 *
 * The original desktop UI uses vtkCellPicker.SetTolerance(0.008) for edges.
 * Drei/Three Line raycasting is not a reliable substitute because its hit area
 * depends on Line2 internals, DPR and camera scale.  Here every B-Rep edge is
 * projected to screen pixels and the closest projected polyline is selected
 * using the same 0.8% viewport-diagonal tolerance concept.
 */
function EdgeScreenPicker({ mesh, enabled, onSelect, onHover }: {
  mesh: CadMesh | null
  enabled: boolean
  onSelect: (selection: Selection) => void
  onHover: (key: string | null) => void
}) {
  const { camera, gl } = useThree()
  const rafRef = useRef<number | null>(null)
  const pendingRef = useRef<PointerEvent | null>(null)

  useEffect(() => {
    const canvas = gl.domElement
    if (!enabled || !mesh?.edges?.length) {
      onHover(null)
      return
    }

    const projected = new THREE.Vector3()

    const pick = (event: PointerEvent): ProjectedHit | null => {
      const rect = canvas.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return null
      const px = event.clientX - rect.left
      const py = event.clientY - rect.top
      // Desktop parity: vtkCellPicker edge tolerance = 0.008 of viewport scale.
      const tolerancePx = Math.max(7, Math.min(14, Math.hypot(rect.width, rect.height) * 0.008))
      camera.updateMatrixWorld()

      let best: ProjectedHit | null = null
      for (const edge of mesh.edges) {
        const values = edge.positions
        if (values.length < 6) continue
        let previous: { x: number; y: number; z: number } | null = null
        for (let i = 0; i < values.length; i += 3) {
          projected.set(values[i], values[i + 1], values[i + 2]).project(camera)
          const current = {
            x: (projected.x * 0.5 + 0.5) * rect.width,
            y: (-projected.y * 0.5 + 0.5) * rect.height,
            z: projected.z,
          }
          if (previous) {
            // Ignore segments completely outside the camera depth range.
            if (!((previous.z > 1 && current.z > 1) || (previous.z < -1 && current.z < -1))) {
              const result = pointSegmentDistance(px, py, previous.x, previous.y, current.x, current.y)
              if (result.distance <= tolerancePx) {
                const depth = previous.z + (current.z - previous.z) * result.t
                // Match a cell picker: screen distance first, then the nearest
                // projected segment when candidates are visually coincident.
                if (
                  !best ||
                  result.distance < best.distancePx - 0.75 ||
                  (Math.abs(result.distance - best.distancePx) <= 0.75 && depth < best.depth)
                ) {
                  best = { edge, distancePx: result.distance, depth }
                }
              }
            }
          }
          previous = current
        }
      }
      return best
    }

    const edgeKey = (edge: EdgeMesh) => `${edge.occurrence_name || ''}::${edge.id}`

    const handleDown = (event: PointerEvent) => {
      if (event.button !== 0) return
      const hit = pick(event)
      if (!hit) {
        onSelect({})
        return
      }
      onSelect({ type: 'edge', edge_ref: hit.edge.id, occurrence_name: hit.edge.occurrence_name || undefined })
      onHover(edgeKey(hit.edge))
      // Deliberately do not stop propagation: the desktop picker performs the
      // selection and still forwards the same press to the trackball camera.
    }

    const flushHover = () => {
      rafRef.current = null
      const event = pendingRef.current
      pendingRef.current = null
      if (!event) return
      const hit = pick(event)
      onHover(hit ? edgeKey(hit.edge) : null)
    }

    const handleMove = (event: PointerEvent) => {
      pendingRef.current = event
      if (rafRef.current === null) rafRef.current = window.requestAnimationFrame(flushHover)
    }

    const handleLeave = () => onHover(null)

    canvas.addEventListener('pointerdown', handleDown, true)
    canvas.addEventListener('pointermove', handleMove, true)
    canvas.addEventListener('pointerleave', handleLeave, true)
    return () => {
      canvas.removeEventListener('pointerdown', handleDown, true)
      canvas.removeEventListener('pointermove', handleMove, true)
      canvas.removeEventListener('pointerleave', handleLeave, true)
      if (rafRef.current !== null) window.cancelAnimationFrame(rafRef.current)
    }
  }, [camera, enabled, gl, mesh, onHover, onSelect])

  return null
}

function CameraController({ mesh, orientation }: { mesh: CadMesh | null; orientation: string }) {
  const { camera, size, invalidate } = useThree()
  const controls = useRef<any>(null)

  useEffect(() => {
    if (!mesh?.bounds) return

    const min = new THREE.Vector3(...mesh.bounds.min)
    const max = new THREE.Vector3(...mesh.bounds.max)
    const center = min.clone().add(max).multiplyScalar(0.5)
    const dimensions = max.clone().sub(min)
    const radius = Math.max(dimensions.length() * 0.5, 0.01)

    // Exact orientation/up-vector semantics from core/render.py
    // set_camera_orientation(), including the non-degenerate Top/Bottom up.
    const table: Record<string, { position: THREE.Vector3; up: THREE.Vector3 }> = {
      front: { position: new THREE.Vector3(0, -1, 0), up: new THREE.Vector3(0, 0, 1) },
      back: { position: new THREE.Vector3(0, 1, 0), up: new THREE.Vector3(0, 0, 1) },
      top: { position: new THREE.Vector3(0, 0, 1), up: new THREE.Vector3(0, 1, 0) },
      bottom: { position: new THREE.Vector3(0, 0, -1), up: new THREE.Vector3(0, 1, 0) },
      left: { position: new THREE.Vector3(-1, 0, 0), up: new THREE.Vector3(0, 0, 1) },
      right: { position: new THREE.Vector3(1, 0, 0), up: new THREE.Vector3(0, 0, 1) },
      iso_top_right: { position: new THREE.Vector3(1, -1, 1), up: new THREE.Vector3(0, 0, 1) },
      isometric: { position: new THREE.Vector3(1, -1, 1), up: new THREE.Vector3(0, 0, 1) },
    }
    const key = orientation.split('|')[0]
    const view = table[key] || table.iso_top_right
    const direction = view.position.clone().normalize()

    let distance = radius * 3
    if (camera instanceof THREE.PerspectiveCamera) {
      const verticalHalfFov = THREE.MathUtils.degToRad(camera.fov * 0.5)
      const aspect = Math.max(size.width / Math.max(size.height, 1), 1e-6)
      const horizontalHalfFov = Math.atan(Math.tan(verticalHalfFov) * aspect)
      const limitingHalfFov = Math.max(THREE.MathUtils.degToRad(5), Math.min(verticalHalfFov, horizontalHalfFov))
      distance = (radius / Math.sin(limitingHalfFov)) * 1.16
      camera.near = Math.max(0.01, distance - radius * 4)
      camera.far = Math.max(1000, distance + radius * 12)
      camera.updateProjectionMatrix()
    }

    camera.position.copy(center.clone().add(direction.multiplyScalar(distance)))
    camera.up.copy(view.up)
    camera.lookAt(center)
    camera.updateMatrixWorld(true)
    if (controls.current) {
      controls.current.target.copy(center)
      controls.current.update()
    }
    invalidate()
  }, [camera, invalidate, mesh?.bounds, mesh?.revision, orientation, size.height, size.width])

  return <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.09} minDistance={0.01} />
}

export default function CadViewer(props: Props) {
  const { mesh, selection, selectionMode, showEdges, showGrid, transparent, sectionAxis, sectionOffset, orientation, theme, onSelect } = props
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null)

  const clipPlane = useMemo(() => {
    if (sectionAxis === 'off' || !mesh?.bounds) return null
    const axis = sectionAxis === 'x' ? new THREE.Vector3(-1, 0, 0) : sectionAxis === 'y' ? new THREE.Vector3(0, -1, 0) : new THREE.Vector3(0, 0, -1)
    const min = mesh.bounds.min[sectionAxis === 'x' ? 0 : sectionAxis === 'y' ? 1 : 2]
    const max = mesh.bounds.max[sectionAxis === 'x' ? 0 : sectionAxis === 'y' ? 1 : 2]
    return new THREE.Plane(axis, min + (max - min) * sectionOffset)
  }, [mesh?.bounds, sectionAxis, sectionOffset])

  const edgeKey = (edge: EdgeMesh) => `${edge.occurrence_name || ''}::${edge.id}`

  return (
    <div className={`cad-canvas ${theme}`}>
      <Canvas
        camera={{ fov: 34, near: 0.01, far: 100000, position: [120, -120, 100] }}
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: false, powerPreference: 'high-performance', preserveDrawingBuffer: true }}
        onCreated={({ gl }) => { gl.localClippingEnabled = true }}
        onPointerMissed={() => { if (selectionMode !== 'edge') onSelect({}) }}
      >
        <color attach="background" args={[theme === 'dark' ? '#0c0f14' : '#edf1f6']} />
        <ambientLight intensity={theme === 'dark' ? 1.5 : 1.7} />
        <directionalLight position={[100, -60, 150]} intensity={2.2} />
        <directionalLight position={[-120, 80, 60]} intensity={0.9} />
        {showGrid && <Grid infiniteGrid fadeDistance={900} fadeStrength={3} cellSize={10} sectionSize={50} cellColor={theme === 'dark' ? '#242c37' : '#ccd3dc'} sectionColor={theme === 'dark' ? '#34445a' : '#abb8c8'} position={[0, 0, 0]} rotation={[Math.PI / 2, 0, 0]} />}
        <group>
          {mesh?.faces.map((face) => (
            <Face key={`${mesh.revision}-${face.occurrence_name || ''}-${face.id}`} face={face} selected={(selection.type === 'face' && selection.face_ref === face.id && (!selection.occurrence_name || selection.occurrence_name === face.occurrence_name)) || (selection.type === 'occurrence' && selection.occurrence_name === face.occurrence_name) || (selection.type === 'part' && !face.occurrence_name)} selectionMode={selectionMode} transparent={transparent} clipPlane={clipPlane} onSelect={onSelect} />
          ))}
          {(showEdges || selectionMode === 'edge') && mesh?.edges.map((edge) => (
            <Edge key={`${mesh.revision}-${edge.occurrence_name || ''}-${edge.id}`} edge={edge} selected={selection.type === 'edge' && selection.edge_ref === edge.id && (!selection.occurrence_name || selection.occurrence_name === edge.occurrence_name)} hovered={selectionMode === 'edge' && hoveredEdge === edgeKey(edge)} />
          ))}
        </group>
        <EdgeScreenPicker mesh={mesh} enabled={selectionMode === 'edge'} onSelect={onSelect} onHover={setHoveredEdge} />
        <CameraController mesh={mesh} orientation={orientation} />
        <GizmoHelper alignment="bottom-right" margin={[76, 72]}><GizmoViewport axisColors={['#f06464', '#57b77d', '#5b8cff']} labelColor={theme === 'dark' ? 'white' : '#17202d'} /></GizmoHelper>
      </Canvas>
      {!mesh && <div className="viewer-loading"><span className="spinner" /> Connecting to 3D kernel…</div>}
      {mesh && mesh.faces.length === 0 && <div className="empty-view"><div className="empty-cube">N</div><strong>Empty CAD Document</strong><span>Enter a prompt in the AI panel or open a STEP file.</span></div>}
      <div className="viewport-badge">OCCT B-REP · {mesh?.faces.length ?? 0} faces</div>
    </div>
  )
}
