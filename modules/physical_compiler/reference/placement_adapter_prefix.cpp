// Input/output adapters only. Algorithms are extracted from pinned upstream.
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <map>
#include <memory>
#include <numeric>
#include <optional>
#include <queue>
#include <ranges>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>
#define NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(...)
#define SPDLOG_TRACE(...)
#define SPDLOG_DEBUG(...)
namespace qc {
using Qubit = std::size_t;
constexpr std::size_t combineHash(std::size_t lhs, std::size_t rhs) noexcept {
  return lhs ^ (rhs + 0x9e3779b97f4a7c15ULL + (lhs << 6) + (lhs >> 2));
}
[[noreturn]] inline void unreachable() { std::abort(); }
}
namespace nlohmann { struct json; }
