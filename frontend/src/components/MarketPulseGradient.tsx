import { ShaderGradient, ShaderGradientCanvas } from '@shadergradient/react'

// A subtle animated glow for the landing page's market-snapshot card --
// colored by the day's actual advance/decline count, not a decorative
// generic gradient. Must sit inside a `position: relative; overflow: hidden`
// parent; render its sibling content with `position: relative; zIndex: 1`
// on top of it (CSS stacking rules put this positioned layer above static
// siblings otherwise).
export function MarketPulseGradient({ bullish }: { bullish: boolean }) {
  const [color1, color2] = bullish ? ['#4bd69b', '#1f6b4d'] : ['#ff7a72', '#7a2f2a']
  return (
    <div style={{ position: 'absolute', inset: 0, opacity: 0.35 }}>
      <ShaderGradientCanvas pointerEvents="none" style={{ width: '100%', height: '100%' }}>
        <ShaderGradient
          control="props"
          type="waterPlane"
          animate="on"
          shader="defaults"
          uSpeed={0.2}
          uStrength={2.6}
          uDensity={1.1}
          uFrequency={0}
          uAmplitude={0}
          positionX={0}
          positionY={0.6}
          positionZ={-0.3}
          rotationX={45}
          rotationY={0}
          rotationZ={0}
          color1={color1}
          color2={color2}
          color3="#0e0e16"
          reflection={0.05}
          cAzimuthAngle={170}
          cPolarAngle={70}
          cDistance={4.4}
          cameraZoom={1}
          lightType="3d"
          brightness={0.6}
          envPreset="city"
          grain="off"
          wireframe={false}
        />
      </ShaderGradientCanvas>
    </div>
  )
}
