shader_type spatial;
// Comfort vignette: darkens the edges of each eye while the player moves or
// turns. Drawn on a quad just in front of the XR camera; the fade is by the
// angle from the view direction, so it works the same in either eye.
render_mode unshaded, depth_test_disabled, cull_disabled, blend_mix, shadows_disabled;

uniform float amount : hint_range(0.0, 1.0) = 0.0;
// Tangent of the angle where the fade starts (about 25 degrees) and where it
// is fully dark (about 45 degrees).
uniform float inner = 0.47;
uniform float outer = 1.0;
uniform vec4 tint : source_color = vec4(0.0, 0.0, 0.0, 1.0);

void fragment() {
	vec3 direction = normalize(VERTEX);
	float off_axis = length(direction.xy) / max(-direction.z, 0.001);
	ALBEDO = tint.rgb;
	ALPHA = smoothstep(inner, outer, off_axis) * amount * tint.a;
}
