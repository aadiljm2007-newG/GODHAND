// Midnight Purple Interactive Liquid Gradient
// Uses Global THREE object

const canvas = document.querySelector('#bg-canvas');
let renderer, scene, camera, particles, backgroundMesh;
let mouseX = 0, mouseY = 0;
let targetX = 0, targetY = 0;
let windowHalfX = window.innerWidth / 2;
let windowHalfY = window.innerHeight / 2;

const PARTICLE_COUNT = 3000;
const COLORS = [
    0xffffff, // White
    0xcccccc, // Light Grey
    0x888888, // Mid Grey
    0x333333, // Dark Grey
    0x0a0a0a, // Deep Charcoal
];

// Shader for Liquid Gradient
const vertexShader = `
    varying vec2 vUv;
    void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
`;

const fragmentShader = `
    uniform float u_time;
    uniform vec2 u_mouse;
    varying vec2 vUv;

    void main() {
        vec2 p = vUv;
        
        // Dynamic liquid displacement
        float noise = sin(p.x * 3.0 + u_time * 0.5) * cos(p.y * 3.0 + u_time * 0.5) * 0.2;
        vec2 liquid = p + noise;
        
        // Distance from mouse for interactive ripple
        float d = length(p - u_mouse);
        float ripple = sin(d * 10.0 - u_time * 2.0) * 0.02 * smoothstep(0.5, 0.0, d);
        liquid += ripple;
        
        // Monochrome Palette
        vec3 col1 = vec3(1.00, 1.00, 1.00); // White
        vec3 col2 = vec3(0.80, 0.80, 0.80); // Light Grey
        vec3 col3 = vec3(0.50, 0.50, 0.50); // Mid Grey
        vec3 col4 = vec3(0.20, 0.20, 0.20); // Dark Grey
        vec3 col5 = vec3(0.04, 0.04, 0.04); // Near Black
        
        // Layering the colors with smooth transitions
        float mix1 = sin(liquid.x * 1.5 + u_time * 0.2) * 0.5 + 0.5;
        float mix2 = cos(liquid.y * 1.5 + u_time * 0.3) * 0.5 + 0.5;
        float mouseEffect = smoothstep(0.4, 0.0, d);
        
        // Solid Black Background
        vec3 finalCol = col1 * mouseEffect * 0.8; // Only show the white interaction glow

        finalCol = mix(finalCol, col1, mouseEffect * 0.8); // White mouse glow
        
        gl_FragColor = vec4(finalCol, 1.0);
    }
`;

function init() {
    // Basic Three.js setup using global THREE
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 1, 1000);
    camera.position.z = 400;

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);

    // 1. Background Liquid Mesh
    const bgGeometry = new THREE.PlaneGeometry(2500, 2500);
    const bgMaterial = new THREE.ShaderMaterial({
        uniforms: {
            u_time: { value: 0 },
            u_mouse: { value: new THREE.Vector2(0.5, 0.5) }
        },
        vertexShader,
        fragmentShader
    });
    backgroundMesh = new THREE.Mesh(bgGeometry, bgMaterial);
    backgroundMesh.position.z = -150;
    scene.add(backgroundMesh);

    // 2. Interactive Particles
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(PARTICLE_COUNT * 3);
    const colors = new Float32Array(PARTICLE_COUNT * 3);

    for (let i = 0; i < PARTICLE_COUNT; i++) {
        positions[i * 3] = (Math.random() - 0.5) * 1500;
        positions[i * 3 + 1] = (Math.random() - 0.5) * 1500;
        positions[i * 3 + 2] = (Math.random() - 0.5) * 1000;

        const color = new THREE.Color(COLORS[Math.floor(Math.random() * COLORS.length)]);
        colors[i * 3] = color.r;
        colors[i * 3 + 1] = color.g;
        colors[i * 3 + 2] = color.b;
    }
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const material = new THREE.PointsMaterial({
        size: 2.5,
        vertexColors: THREE.VertexColors, // Older versions use this, newer use vertexColors: true
        transparent: true,
        opacity: 0.5,
        blending: THREE.AdditiveBlending
    });

    particles = new THREE.Points(geometry, material);
    scene.add(particles);

    const handleMouseMove = (e) => {
        targetX = e.clientX - windowHalfX;
        targetY = e.clientY - windowHalfY;

        // Update shader mouse uniform (inverted Y for GLSL coordinate system)
        backgroundMesh.material.uniforms.u_mouse.value.set(
            e.clientX / window.innerWidth,
            1.0 - e.clientY / window.innerHeight
        );
    };

    document.addEventListener('mousemove', handleMouseMove);

    window.addEventListener('resize', () => {
        windowHalfX = window.innerWidth / 2;
        windowHalfY = window.innerHeight / 2;
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(window.innerWidth, window.innerHeight);
    });

    animate();
}

function animate() {
    requestAnimationFrame(animate);

    const time = Date.now() * 0.001;
    backgroundMesh.material.uniforms.u_time.value = time;

    // Smooth camera interpolation
    mouseX += (targetX - mouseX) * 0.05;
    mouseY += (targetY - mouseY) * 0.05;
    camera.position.x += (mouseX * 0.15 - camera.position.x) * 0.05;
    camera.position.y += (-mouseY * 0.15 - camera.position.y) * 0.05;
    camera.lookAt(scene.position);

    // Particle subtle drift
    particles.rotation.y = time * 0.05;
    particles.rotation.z = time * 0.02;

    renderer.render(scene, camera);
}

init();
