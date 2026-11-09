// Keeps Parakeet loaded on the GPU between requests (parakeet-cli reloads it every time: 1.5-6 s).
// Used by voice/ears.py. Reads from stdin: a uint32 sample count, then 16-bit 16 kHz mono samples.
// Writes one line per request: confidence (mean log token probability), a tab, then the text.
//
// Build (against the whisper.cpp in ~/.local/share/jarvis, after `cmake --build build --target parakeet-cli`):
//   W=~/.local/share/jarvis/whisper.cpp; g++ -O2 -std=c++17 -I$W/include -I$W/ggml/include tools/parakeet-serve.cpp \
//     -o $W/build/bin/parakeet-serve -L$W/build/bin -lparakeet -lggml -lggml-base -lpthread -Wl,-rpath,$W/build/bin
#include "parakeet.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

static void silent(enum ggml_log_level, const char *, void *) {}

int main(int argc, char ** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s MODEL [THREADS]\n", argv[0]);
        return 2;
    }
    const int threads = argc > 2 ? atoi(argv[2]) : 4;
    ggml_backend_load_all();
    parakeet_log_set(silent, nullptr);
    struct parakeet_context_params cparams = parakeet_context_default_params();
    cparams.use_gpu = true;
    struct parakeet_context * ctx = parakeet_init_from_file_with_params(argv[1], cparams);
    if (ctx == nullptr) {
        fprintf(stderr, "could not load %s\n", argv[1]);
        return 1;
    }
    puts("ready");
    fflush(stdout);

    uint32_t n;
    while (fread(&n, sizeof(n), 1, stdin) == 1) {
        std::vector<int16_t> pcm(n);
        if (fread(pcm.data(), sizeof(int16_t), n, stdin) != n) {
            break;
        }
        std::vector<float> samples(n);
        for (uint32_t i = 0; i < n; i++) {
            samples[i] = pcm[i] / 32768.0f;
        }
        struct parakeet_full_params params = parakeet_full_default_params(PARAKEET_SAMPLING_GREEDY);
        params.n_threads = threads;
        std::string text;
        double total = 0;
        int count = 0;
        if (n > 0 && parakeet_full(ctx, params, samples.data(), samples.size()) == 0) {
            for (int s = 0; s < parakeet_full_n_segments(ctx); s++) {
                text += parakeet_full_get_segment_text(ctx, s);
                for (int t = 0; t < parakeet_full_n_tokens(ctx, s); t++) {
                    total += std::log(std::max(parakeet_full_get_token_data(ctx, s, t).p, 1e-6f));
                    count++;
                }
            }
        }
        for (auto & c : text) {
            if (c == '\n' || c == '\t') c = ' ';
        }
        printf("%.4f\t%s\n", count ? total / count : 0.0, text.c_str());
        fflush(stdout);
    }
    parakeet_free(ctx);
    return 0;
}
