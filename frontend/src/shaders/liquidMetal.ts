// Ported from https://github.com/collidingScopes/liquid-logo (MIT).
// Vertex shader adapted to three.js: uses the built-in `position` attribute
// on a full-screen [-1,1] quad instead of the original's custom aVertexPosition,
// and bypasses the camera/model matrices entirely (matches the source's raw NDC pass-through).
export const liquidMetalVertexShader = /* glsl */ `
void main() {
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`

export const liquidMetalFragmentShader = /* glsl */ `
precision highp float;

uniform vec2 u_resolution;
uniform float u_time;

uniform float u_speed;
uniform float u_iterations;
uniform float u_scale;
uniform float u_dotFactor;
uniform float u_vOffset;
uniform float u_intensityFactor;
uniform float u_expFactor;
uniform vec3 u_colorFactors;
uniform float u_colorShift;
uniform float u_dotMultiplier;
uniform float u_noiseIntensity;

uniform sampler2D u_logoTexture;
uniform float u_logoOpacity;
uniform float u_logoScale;
uniform float u_logoAspectRatio;
uniform float u_logoInteractStrength;
uniform int u_logoBlendMode;

float random(vec2 st) {
    return fract(sin(dot(st.xy, vec2(12.9898, 78.233))) * 43758.5453123);
}

vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
    const vec2 C = vec2(1.0/6.0, 1.0/3.0);
    const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);

    vec3 i  = floor(v + dot(v, C.yyy));
    vec3 x0 = v - i + dot(i, C.xxx);

    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min(g.xyz, l.zxy);
    vec3 i2 = max(g.xyz, l.zxy);

    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + C.yyy;
    vec3 x3 = x0 - D.yyy;

    i = mod289(i);
    vec4 p = permute(permute(permute(
              i.z + vec4(0.0, i1.z, i2.z, 1.0))
            + i.y + vec4(0.0, i1.y, i2.y, 1.0))
            + i.x + vec4(0.0, i1.x, i2.x, 1.0));

    float n_ = 0.142857142857;
    vec3 ns = n_ * D.wyz - D.xzx;

    vec4 j = p - 49.0 * floor(p * ns.z * ns.z);

    vec4 x_ = floor(j * ns.z);
    vec4 y_ = floor(j - 7.0 * x_);

    vec4 x = x_ *ns.x + ns.yyyy;
    vec4 y = y_ *ns.x + ns.yyyy;
    vec4 h = 1.0 - abs(x) - abs(y);

    vec4 b0 = vec4(x.xy, y.xy);
    vec4 b1 = vec4(x.zw, y.zw);

    vec4 s0 = floor(b0)*2.0 + 1.0;
    vec4 s1 = floor(b1)*2.0 + 1.0;
    vec4 sh = -step(h, vec4(0.0));

    vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
    vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;

    vec3 p0 = vec3(a0.xy, h.x);
    vec3 p1 = vec3(a0.zw, h.y);
    vec3 p2 = vec3(a1.xy, h.z);
    vec3 p3 = vec3(a1.zw, h.w);

    vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
    p0 *= norm.x;
    p1 *= norm.y;
    p2 *= norm.z;
    p3 *= norm.w;

    vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
    m = m * m;
    return 42.0 * dot(m*m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}

float detectEdges(vec2 uv, float threshold) {
    float dx = 1.0 / u_resolution.x;
    float dy = 1.0 / u_resolution.y;

    vec4 center = texture2D(u_logoTexture, uv);
    vec4 left = texture2D(u_logoTexture, uv - vec2(dx, 0.0));
    vec4 right = texture2D(u_logoTexture, uv + vec2(dx, 0.0));
    vec4 top = texture2D(u_logoTexture, uv - vec2(0.0, dy));
    vec4 bottom = texture2D(u_logoTexture, uv + vec2(0.0, dy));

    float diff = length(center - left) + length(center - right) +
                length(center - top) + length(center - bottom);

    return smoothstep(0.0, threshold, diff);
}

vec4 liquidMetalEffect(vec4 color, float edge, float time) {
    float highlight = pow(0.5 + 0.5 * sin(time * 0.0 + edge * 6.0), 8.0) * edge;

    vec4 metallic = vec4(
        color.r + highlight * 0.4,
        color.g + highlight * 0.3,
        color.b + highlight * 0.5,
        color.a
    );

    float ripple = sin(edge * 15.0 + time) * 0.00;
    metallic.rgb += ripple;

    return clamp(metallic, 0.0, 1.0);
}

vec4 applyBlendMode(vec4 base, vec4 blend, int mode, float opacity) {
    vec4 result;

    if (mode == 1) {
        result = base * blend;
    } else if (mode == 2) {
        result = 1.0 - (1.0 - base) * (1.0 - blend);
    } else if (mode == 3) {
        vec4 check = step(0.5, base);
        result = mix(2.0 * base * blend, 1.0 - 2.0 * (1.0 - base) * (1.0 - blend), check);
    } else {
        result = blend;
    }

    return mix(base, result, opacity);
}

vec2 applyNoiseDistortion(vec2 uv, float time, float strength) {
    float noise1 = snoise(vec3(uv * 3.0, time * 0.1)) * 0.01 * strength;
    float noise2 = snoise(vec3(uv * 7.0, time * 0.2 + 100.0)) * 0.005 * strength;

    vec2 offset = vec2(
        noise1 + noise2 * 0.5,
        noise1 * 0.8 + noise2
    );

    return uv + offset;
}

void main() {
    vec2 r = u_resolution;
    vec2 FC = gl_FragCoord.xy;
    float time = u_time * u_speed;

    vec2 uv = FC.xy / r;

    vec2 logoUV = (uv - 0.5) / u_logoScale + 0.5;
    logoUV.y = 1.0 - logoUV.y;

    vec4 logoColor = texture2D(u_logoTexture, logoUV);
    float logoAlpha = logoColor.a;

    bool insideLogo = logoAlpha > 0.1;

    if (!insideLogo && logoUV.x >= 0.0 && logoUV.x <= 1.0 && logoUV.y >= 0.0 && logoUV.y <= 1.0) {
        gl_FragColor = vec4(0.0, 0.0, 0.0, 0.0);
        return;
    }

    float edge = detectEdges(logoUV, 0.2) * u_logoInteractStrength;

    vec2 p = (FC.xy*2.0-r)/r.y;
    vec2 l = vec2(0.0);
    float dotP = dot(p, p);
    l.x += abs(u_dotFactor-dotP) * u_dotMultiplier;

    float edgeInfluence = edge * 20.0;
    vec2 v = p*(1.0-l.x)/u_scale;

    v += vec2(sin(edge * 10.0), cos(edge * 8.0)) * edgeInfluence;

    float noiseIntensity = insideLogo ? u_noiseIntensity : 0.1;
    float flowNoise = snoise(vec3(p * 2.0, time * 0.15)) * noiseIntensity;
    v += vec2(flowNoise, flowNoise * 0.7);

    vec4 o = vec4(0.0);
    for(float i = 0.0; i < 16.0; i++) {
        if (i >= u_iterations) break;
        float idx = i + 1.0;

        vec2 offset = cos(v.yx*idx+vec2(0.0,idx)+time)/idx+u_vOffset;
        if (logoAlpha > 0.1 && edge > 0.1) {
            offset *= 1.0 + edge * 4.0;
        }

        v += offset;
        o += (sin(vec4(v.x,v.y,v.y,v.x))+1.0)*abs(v.x-v.y)*u_intensityFactor;
    }

    if (u_colorShift > 0.0) {
        o = o.wxyz * u_colorShift + o * (1.0 - u_colorShift);
    }

    vec4 expPy = exp(p.y*vec4(u_colorFactors.x, u_colorFactors.y, u_colorFactors.z, 0.0));
    float expLx = exp(-u_expFactor*l.x);
    vec4 ratio = expPy*expLx/o;

    vec4 exp2x = exp(2.0 * ratio);
    o = (exp2x - 1.0) / (exp2x + 1.0);

    vec2 noiseCoord = FC / 1.5;
    float noise = random(noiseCoord + time * 0.0004) * 0.12 - 0.075;
    o = o + vec4(noise);

    o = liquidMetalEffect(o, edge, time);

    o = clamp(o, 0.0, 1.0);

    if (logoUV.x >= 0.0 && logoUV.x <= 1.0 && logoUV.y >= 0.0 && logoUV.y <= 1.0) {
        if (insideLogo) {
            vec4 finalColor = mix(o, vec4(o.rgb * 0.8 + 0.2, logoAlpha), 0.3);

            float highlight = pow(edge * 1.2, 4.0);
            finalColor.rgb += highlight * vec3(0.6, 0.7, 0.8);
            finalColor.a = min(finalColor.a + 0.4, 1.0);
            gl_FragColor = finalColor;
        } else {
            discard;
        }
    } else {
        discard;
    }
}
`
