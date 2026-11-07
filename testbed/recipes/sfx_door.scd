// Door SFX, synthesised in SuperCollider. Replayed by `joinery rebuild`.
//
// Operation log for audio/sfx/door_creak.wav and audio/sfx/door_close.wav.
// Both sounds are built from oscillators, noise and envelopes: no samples, no
// libraries, nothing fetched. The patch *is* the source, so editing these
// numbers and re-rendering is how the sound changes.
//
// Rendered non-real-time, so the result does not depend on an audio device and
// the same bytes come out every run. That is what lets the asset gate compare
// renders exactly instead of approximately.
//
// Sound palette: dry and close, no reverb tail, nothing cartoonish.
// SFX peaks are held under -1 dBTP by a limiter on the output.
//
// recordNRT is asynchronous, so each render hands off to the next through its
// action callback and the last one exits. Waiting at the top level instead
// would hang: `wait` only works inside a Routine, and sclang would sit there
// with the renders unfinished.
//
// Run: sclang recipes/sfx_door.scd

(
var sampleRate = 48000;
var options = ServerOptions.new.numOutputBusChannels_(1);
var jobs, runJob;

// A creak: stick-slip friction. A slow pitch ramp through a resonant filter,
// amplitude-modulated by noise so it stutters like dry hinge metal.
SynthDef(\joinery_creak, { |dur = 1.6|
    var env, grind, body, sig;
    // Seed the server's RNG so the noise is the same every render. Without
    // this the patch is musically identical but byte-different each run, and
    // an asset that cannot be reproduced exactly cannot be verified exactly.
    RandID.ir(0);
    RandSeed.ir(1, 20261107);
    env = EnvGen.kr(Env([0, 1, 0.8, 0], [0.04, dur - 0.3, 0.26], \sin));
    grind = LFNoise1.kr(34).range(0.35, 1.0) * LFNoise2.kr(7).range(0.6, 1.0);
    body = Mix(Saw.ar(Line.kr(118, 196, dur) * [1, 1.502, 2.004])) * 0.33;
    sig = BPF.ar(body * grind, Line.kr(620, 1180, dur), 0.18) * 6;
    sig = sig + (BPF.ar(PinkNoise.ar(0.22) * grind, 2300, 0.35) * 2.2);
    sig = LPF.ar(sig * env, 7200);
    Out.ar(0, Limiter.ar(sig * 0.9, 0.89));
}).store;

// A close: wooden thud plus a latch click 42 ms later. Two transients, no ring.
SynthDef(\joinery_close, {
    var thud, click, latch, sig;
    RandID.ir(0);
    RandSeed.ir(1, 884422);
    thud = LPF.ar(PinkNoise.ar(1) * EnvGen.ar(Env.perc(0.004, 0.17, 1, -5)), 260) * 1.5;
    thud = thud + (SinOsc.ar(XLine.kr(132, 68, 0.19))
        * EnvGen.ar(Env.perc(0.002, 0.2, 1, -6)) * 0.55);
    click = BPF.ar(WhiteNoise.ar(1) * EnvGen.ar(Env.perc(0.001, 0.035, 1, -8)),
        3400, 0.22) * 0.5;
    latch = DelayN.ar(click, 0.1, 0.042);
    sig = (thud + latch) * EnvGen.kr(Env.linen(0.001, 0.26, 0.05));
    Out.ar(0, Limiter.ar(LPF.ar(sig, 9000), 0.89));
}).store;

File.mkdir("audio");
File.mkdir("audio/sfx");

// name, duration, output
jobs = [
    [\joinery_creak, 1.9, "audio/sfx/door_creak.wav"],
    [\joinery_close, 0.42, "audio/sfx/door_close.wav"]
];

runJob = { |index|
    // 0.exit does not stop the function, so the remaining work has to live in
    // the else branch; otherwise this falls through and indexes past the end.
    if (index >= jobs.size) {
        "JOINERY_BUILT audio/sfx/door_creak.wav audio/sfx/door_close.wav".postln;
        0.exit;
    } {
        var name, dur, out, score;
        # name, dur, out = jobs[index];
        score = Score([
            [0.0, Synth.basicNew(name, nil, 1000).newMsg],
            [dur, [\c_set, 0, 0]]
        ]);
        ("rendering " ++ out).postln;
        score.recordNRT(
            outputFilePath: out.standardizePath,
            sampleRate: sampleRate,
            headerFormat: "WAV",
            sampleFormat: "int16",
            options: options,
            duration: dur,
            action: { runJob.value(index + 1) }
        );
    };
};

runJob.value(0);
)
