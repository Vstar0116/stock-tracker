import { Canvas, useFrame } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'

function readColor(varName: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
  return v || fallback
}

function usePointerRef() {
  const pointerRef = useRef({ x: 0, y: 0 })
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      pointerRef.current.x = (e.clientX / window.innerWidth) * 2 - 1
      pointerRef.current.y = -((e.clientY / window.innerHeight) * 2 - 1)
    }
    window.addEventListener('pointermove', onMove)
    return () => window.removeEventListener('pointermove', onMove)
  }, [])
  return pointerRef
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])
  return matches
}

function ParticleField({
  pointerRef,
  count,
  color,
  reducedMotion,
}: {
  pointerRef: React.RefObject<{ x: number; y: number }>
  count: number
  color: string
  reducedMotion: boolean
}) {
  const groupRef = useRef<THREE.Points>(null)

  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      arr[i * 3] = (Math.random() - 0.5) * 16
      arr[i * 3 + 1] = (Math.random() - 0.5) * 10
      arr[i * 3 + 2] = (Math.random() - 0.5) * 8
    }
    return arr
  }, [count])

  useFrame(() => {
    if (reducedMotion || !groupRef.current) return
    const { x, y } = pointerRef.current
    groupRef.current.rotation.y += (x * 0.15 - groupRef.current.rotation.y) * 0.02
    groupRef.current.rotation.x += (y * 0.1 - groupRef.current.rotation.x) * 0.02
  })

  return (
    <points ref={groupRef}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial color={color} size={0.035} transparent opacity={0.5} sizeAttenuation />
    </points>
  )
}

function DraggableMark({
  rotationRef,
  color,
  reducedMotion,
}: {
  rotationRef: React.RefObject<{ x: number; y: number }>
  color: string
  reducedMotion: boolean
}) {
  const meshRef = useRef<THREE.Mesh>(null)

  useFrame(({ viewport }) => {
    if (!meshRef.current) return
    meshRef.current.position.set(viewport.width / 2 - 1.4, -viewport.height / 2 + 1.4, 0)
    const target = rotationRef.current
    meshRef.current.rotation.x += (target.x - meshRef.current.rotation.x) * 0.15
    meshRef.current.rotation.y += (target.y - meshRef.current.rotation.y) * 0.15
    if (!reducedMotion) {
      meshRef.current.rotation.y += 0.002
    }
  })

  return (
    <mesh ref={meshRef}>
      <icosahedronGeometry args={[1, 0]} />
      <meshBasicMaterial color={color} wireframe transparent opacity={0.6} />
    </mesh>
  )
}

export function Scene3D() {
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const isCoarsePointer = useMediaQuery('(pointer: coarse)')
  const pointerRef = usePointerRef()
  const dragRotationRef = useRef({ x: 0, y: 0 })

  const accentColor = useMemo(() => readColor('--color-accent-700', '#a996ff'), [])
  const brandColor = useMemo(() => readColor('--color-brand', '#6e55ff'), [])
  const particleCount = isCoarsePointer ? 220 : 700

  const dragState = useRef<{ id: number; lastX: number; lastY: number } | null>(null)

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    dragState.current = { id: e.pointerId, lastX: e.clientX, lastY: e.clientY }
  }
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragState.current || dragState.current.id !== e.pointerId) return
    const dx = e.clientX - dragState.current.lastX
    const dy = e.clientY - dragState.current.lastY
    dragState.current.lastX = e.clientX
    dragState.current.lastY = e.clientY
    dragRotationRef.current = {
      x: dragRotationRef.current.x + dy * 0.01,
      y: dragRotationRef.current.y + dx * 0.01,
    }
  }
  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragState.current?.id === e.pointerId) dragState.current = null
  }

  return (
    <>
      <div
        className="scene3d-root"
        style={{ position: 'fixed', inset: 0, zIndex: -1, pointerEvents: 'none' }}
      >
        <Canvas
          dpr={[1, 1.5]}
          gl={{ alpha: true, antialias: true }}
          camera={{ position: [0, 0, 6], fov: 45 }}
        >
          <ParticleField
            pointerRef={pointerRef}
            count={particleCount}
            color={accentColor}
            reducedMotion={reducedMotion}
          />
          <DraggableMark rotationRef={dragRotationRef} color={brandColor} reducedMotion={reducedMotion} />
        </Canvas>
      </div>
      <div
        className="scene3d-hit-region"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      />
    </>
  )
}
