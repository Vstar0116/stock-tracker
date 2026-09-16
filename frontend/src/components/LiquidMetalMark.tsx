import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import { liquidMetalFragmentShader, liquidMetalVertexShader } from '../shaders/liquidMetal'

function makeGlyphTexture(glyph: string): THREE.CanvasTexture {
  const size = 256
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#ffffff'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.font = `700 ${size * 0.6}px system-ui, sans-serif`
  ctx.fillText(glyph, size / 2, size / 2 + size * 0.03)
  const texture = new THREE.CanvasTexture(canvas)
  texture.needsUpdate = true
  return texture
}

// "Chrome" preset from the source project -- a metallic sheen fits a
// finance brand mark better than the demo's neon/rainbow presets.
function LiquidMetalPlane({ texture }: { texture: THREE.Texture }) {
  const { size } = useThree()

  const uniforms = useMemo(
    () => ({
      u_time: { value: 0 },
      u_resolution: { value: new THREE.Vector2(size.width, size.height) },
      u_speed: { value: 0.4 },
      u_iterations: { value: 15 },
      u_scale: { value: 3.12 },
      u_dotFactor: { value: 0.04 },
      u_dotMultiplier: { value: 0.21 },
      u_vOffset: { value: 5.1 },
      u_intensityFactor: { value: 0.07 },
      u_expFactor: { value: 0.2 },
      u_colorFactors: { value: new THREE.Vector3(1.1, 0.7, 0.9) },
      u_colorShift: { value: 0.9 },
      u_noiseIntensity: { value: 0.35 },
      u_logoTexture: { value: texture },
      u_logoOpacity: { value: 1 },
      u_logoScale: { value: 0.92 },
      u_logoAspectRatio: { value: 1 },
      u_logoInteractStrength: { value: 0.4 },
      u_logoBlendMode: { value: 0 },
    }),
    [texture],
  )

  useEffect(() => {
    uniforms.u_resolution.value.set(size.width, size.height)
  }, [size, uniforms])

  useFrame((_, delta) => {
    uniforms.u_time.value += delta
  })

  return (
    <mesh>
      <planeGeometry args={[2, 2]} />
      <shaderMaterial
        vertexShader={liquidMetalVertexShader}
        fragmentShader={liquidMetalFragmentShader}
        uniforms={uniforms}
        transparent
      />
    </mesh>
  )
}

// Small self-contained liquid-metal brand mark, ported from
// github.com/collidingScopes/liquid-logo -- driven off our own R3F/three
// stack, no new dependency. Renders in its own tiny Canvas so it can drop
// into the sidebar/login badge slot without touching the page background.
export function LiquidMetalMark({ size = 32, glyph = '₹' }: { size?: number; glyph?: string }) {
  const texture = useMemo(() => makeGlyphTexture(glyph), [glyph])
  useEffect(() => () => texture.dispose(), [texture])

  return (
    <div style={{ width: size, height: size, flex: 'none', overflow: 'hidden', borderRadius: size * 0.34 }}>
      <Canvas gl={{ alpha: true, antialias: true }} dpr={[1, 2]}>
        <LiquidMetalPlane texture={texture} />
      </Canvas>
    </div>
  )
}
