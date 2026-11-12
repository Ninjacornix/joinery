// The terrain's splat material: up to four layers from the project's own
// textures (or plain colours), blended by the painted mask (r, g, b = layers
// 1-3) and by each layer's height and slope band. Layer 0 is the ground under
// everything. Set by Terrain from its `layers`; see GUIDE.md.
shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_back, diffuse_burley, specular_schlick_ggx;

uniform sampler2D splat_mask : hint_default_black, filter_linear, repeat_disable;
uniform vec2 terrain_size = vec2(64.0, 64.0);

uniform sampler2D albedo_0 : source_color, hint_default_white, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D albedo_1 : source_color, hint_default_white, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D albedo_2 : source_color, hint_default_white, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D albedo_3 : source_color, hint_default_white, filter_linear_mipmap_anisotropic, repeat_enable;
uniform vec4 color_0 : source_color = vec4(0.42, 0.5, 0.37, 1.0);
uniform vec4 color_1 : source_color = vec4(0.55, 0.47, 0.36, 1.0);
uniform vec4 color_2 : source_color = vec4(0.5, 0.5, 0.52, 1.0);
uniform vec4 color_3 : source_color = vec4(0.92, 0.93, 0.95, 1.0);
// Texture repeats per metre.
uniform vec4 uv_scale = vec4(0.25);
// Per layer 1-3: lowest and highest height (metres), least and most slope (degrees).
uniform vec4 rule_1 = vec4(-100000.0, 100000.0, 0.0, 90.0);
uniform vec4 rule_2 = vec4(-100000.0, 100000.0, 0.0, 90.0);
uniform vec4 rule_3 = vec4(-100000.0, 100000.0, 0.0, 90.0);
// Per layer 1-3: 0 = painted mask only, 1 = the height and slope rule also applies.
uniform vec3 rule_on = vec3(0.0);
uniform float height_blend = 1.0;
uniform float slope_blend = 4.0;
uniform vec4 roughness = vec4(0.9);

varying float slope_degrees;

void vertex() {
	slope_degrees = degrees(acos(clamp(NORMAL.y, -1.0, 1.0)));
}

float band(float value, float low, float high, float soft) {
	return smoothstep(low - soft, low + soft, value) * (1.0 - smoothstep(high - soft, high + soft, value));
}

float rule(vec4 r, float h, float s) {
	return band(h, r.x, r.y, height_blend) * band(s, r.z, r.w, slope_blend);
}

void fragment() {
	// UV is the point's terrain-local x, z in metres; UV2.x its height.
	vec2 here = UV;
	float h = UV2.x;
	vec4 painted = texture(splat_mask, (here + terrain_size * 0.5) / terrain_size);
	float w1 = max(painted.r, rule(rule_1, h, slope_degrees) * rule_on.x);
	float w2 = max(painted.g, rule(rule_2, h, slope_degrees) * rule_on.y);
	float w3 = max(painted.b, rule(rule_3, h, slope_degrees) * rule_on.z);
	vec3 colour = texture(albedo_0, here * uv_scale.x).rgb * color_0.rgb;
	colour = mix(colour, texture(albedo_1, here * uv_scale.y).rgb * color_1.rgb, w1);
	colour = mix(colour, texture(albedo_2, here * uv_scale.z).rgb * color_2.rgb, w2);
	colour = mix(colour, texture(albedo_3, here * uv_scale.w).rgb * color_3.rgb, w3);
	float rough = roughness.x;
	rough = mix(rough, roughness.y, w1);
	rough = mix(rough, roughness.z, w2);
	rough = mix(rough, roughness.w, w3);
	ALBEDO = colour;
	ROUGHNESS = rough;
}
