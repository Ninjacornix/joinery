// Exploration loop, composed as notes and rendered through a synth patch.
// Replayed by `joinery rebuild`.
//
// Operation log for audio/music/explore_loop.wav. The composition is the note
// data below: a 4-bar progression in A aeolian at 84 BPM, with a pad, a bass
// and a sparse bell motif. The patches are oscillators and filters. No samples,
// no libraries, nothing generated.
//
// The loop is seamless: total duration is an exact multiple of the bar length
// and every voice's release fits inside the loop, so the end meets the start.
//
// Loudness target for music is -16 LUFS; this renders at a conservative level
// and leaves final normalisation to the import step rather than baking it in.
//
// The server RNG is seeded, so the render is byte-identical every time.
//
// Run: sclang recipes/music_explore.scd

(
var sampleRate = 48000;
var options = ServerOptions.new.numOutputBusChannels_(2);
var bpm = 84, beatsPerBar = 4, bars = 4;
var beat = 60 / bpm;
var barDur = beat * beatsPerBar;
var total = barDur * bars;
var out = "audio/music/explore_loop.wav";
var score, events;

// A aeolian: A B C D E F G. Root A1/A2 for bass, triads for the pad.
var chords = [
    [57, 60, 64],  // Am
    [53, 57, 60],  // F
    [55, 59, 62],  // G
    [53, 57, 60]   // F
];
var bassNotes = [33, 29, 31, 29];
// Bell motif: [bar, beat offset, midi note]
var motif = [
    [0, 0.0, 76], [0, 2.5, 72],
    [1, 1.0, 69], [1, 3.0, 72],
    [2, 0.5, 74], [2, 2.0, 76],
    [3, 1.5, 72]
];

SynthDef(\joinery_pad, { |freq = 220, dur = 2, amp = 0.1|
    var env, sig;
    RandID.ir(0);
    RandSeed.ir(1, 90210);
    env = EnvGen.kr(Env([0, 1, 0.9, 0], [dur * 0.3, dur * 0.4, dur * 0.3], \sin),
        doneAction: 2);
    // Two slightly detuned saws per note: movement without chorus plugins.
    sig = Mix(Saw.ar(freq * [0.999, 1.001, 2.0] , [0.5, 0.5, 0.18]));
    sig = LPF.ar(sig, freq * 6 + SinOsc.kr(0.07).range(0, 220));
    sig = sig * env * amp;
    Out.ar(0, Pan2.ar(sig, SinOsc.kr(0.03).range(-0.25, 0.25)));
}).store;

SynthDef(\joinery_bass, { |freq = 55, dur = 2, amp = 0.22|
    var env, sig;
    env = EnvGen.kr(Env([0, 1, 0.75, 0], [0.02, dur * 0.5, dur * 0.45], \sin),
        doneAction: 2);
    sig = SinOsc.ar(freq) + (Pulse.ar(freq, 0.35) * 0.25);
    sig = LPF.ar(sig, 420) * env * amp;
    Out.ar(0, Pan2.ar(sig, 0));
}).store;

SynthDef(\joinery_bell, { |freq = 660, amp = 0.09|
    var env, sig;
    env = EnvGen.kr(Env.perc(0.004, 1.1, 1, -6), doneAction: 2);
    // A struck tone: a few inharmonic partials, no sample needed.
    sig = Mix(SinOsc.ar(freq * [1, 2.76, 5.4], 0, [0.6, 0.2, 0.08]));
    sig = sig * env * amp;
    Out.ar(0, Pan2.ar(sig, 0.18));
}).store;

File.mkdir("audio");
File.mkdir("audio/music");

events = List.new;
bars.do { |bar|
    var t = bar * barDur;
    chords[bar].do { |note|
        events.add([t, Synth.basicNew(\joinery_pad, nil, 1000 + events.size)
            .newMsg(args: [\freq, note.midicps, \dur, barDur, \amp, 0.085])]);
    };
    events.add([t, Synth.basicNew(\joinery_bass, nil, 2000 + bar)
        .newMsg(args: [\freq, bassNotes[bar].midicps, \dur, barDur, \amp, 0.2])]);
};
motif.do { |entry, i|
    var t = (entry[0] * barDur) + (entry[1] * beat);
    events.add([t, Synth.basicNew(\joinery_bell, nil, 3000 + i)
        .newMsg(args: [\freq, entry[2].midicps, \amp, 0.075])]);
};
events = events.asArray.sort { |a, b| a[0] <= b[0] };

score = Score(events ++ [[total, [\c_set, 0, 0]]]);
("rendering " ++ out ++ " (" ++ total.round(0.01) ++ "s, " ++ bpm ++ " BPM)").postln;
score.recordNRT(
    outputFilePath: out.standardizePath,
    sampleRate: sampleRate,
    headerFormat: "WAV",
    sampleFormat: "int16",
    options: options,
    duration: total,
    action: {
        ("JOINERY_BUILT " ++ out).postln;
        0.exit;
    }
);
)
