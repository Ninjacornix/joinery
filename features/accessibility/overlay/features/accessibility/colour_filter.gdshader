// Colour-blind filter (daltonization): what someone with protanopia (mode 1),
// deuteranopia (2) or tritanopia (3) can't tell apart is shifted into colours they
// can. The picture is simulated for that colour vision in LMS space (Brettel,
// Vienot and Mollon's model as used by daltonize.js); the lost difference is moved
// into the channels that still work and added back. Mode 0 leaves the picture be.
shader_type canvas_item;

uniform sampler2D screen_texture : hint_screen_texture, filter_linear_mipmap;
uniform int mode = 0;

vec3 simulate(vec3 c) {
	float l = 17.8824 * c.r + 43.5161 * c.g + 4.11935 * c.b;
	float m = 3.45565 * c.r + 27.1554 * c.g + 3.86714 * c.b;
	float s = 0.0299566 * c.r + 0.184309 * c.g + 1.46709 * c.b;
	if (mode == 1) {
		l = 2.02344 * m - 2.52581 * s;
	} else if (mode == 2) {
		m = 0.494207 * l + 1.24827 * s;
	} else if (mode == 3) {
		s = -0.395913 * l + 0.801109 * m;
	}
	return vec3(
		0.0809444479 * l - 0.130504409 * m + 0.116721066 * s,
		-0.0102485335 * l + 0.0540193266 * m - 0.113614708 * s,
		-0.000365296938 * l - 0.00412161469 * m + 0.693511405 * s
	);
}

void fragment() {
	vec4 picture = texture(screen_texture, SCREEN_UV);
	vec3 lost = picture.rgb - simulate(picture.rgb);
	vec3 shifted = vec3(0.0, 0.7 * lost.r + lost.g, 0.7 * lost.r + lost.b);
	COLOR = vec4(clamp(picture.rgb + shifted, 0.0, 1.0), 1.0);
}
