#pragma once
// SHA-256 and HMAC-SHA256 (FIPS 180-4, RFC 2104), self-contained so the
// command check runs identically on the ESP32 and in the PC tests. Checked
// against the RFC 4231 test vectors in firmware/test/test_core.
#include <stddef.h>
#include <stdint.h>

namespace hestia {

class Sha256 {
   public:
    static constexpr size_t DIGEST_SIZE = 32;
    static constexpr size_t BLOCK_SIZE = 64;

    Sha256() { reset(); }
    void reset();
    void update(const uint8_t* data, size_t length);
    void update(const char* text);  // a NUL-terminated string
    void finish(uint8_t digest[DIGEST_SIZE]);

   private:
    void compress(const uint8_t block[BLOCK_SIZE]);
    uint32_t state_[8];
    uint64_t bit_count_;
    uint8_t buffer_[BLOCK_SIZE];
    size_t buffered_;
};

class HmacSha256 {
   public:
    HmacSha256(const uint8_t* key, size_t key_length);
    void update(const uint8_t* data, size_t length) { inner_.update(data, length); }
    void update(const char* text) { inner_.update(text); }
    void finish(uint8_t mac[Sha256::DIGEST_SIZE]);

   private:
    Sha256 inner_;
    uint8_t outer_key_[Sha256::BLOCK_SIZE];
};

}  // namespace hestia
