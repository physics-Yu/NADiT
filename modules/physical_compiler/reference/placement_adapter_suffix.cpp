// Observers wrap original scoring bodies without changing their inputs/results.
namespace na::zoned {
using P = HeuristicPlacer;
std::vector<P::GateJob> observedGateJobs;
std::vector<P::AtomJob> observedAtomJobs;
struct ObservedScore {
  std::vector<size_t> path;
  float cost, heuristic;
  std::array<float, 2> scale;
};
std::map<std::vector<size_t>, ObservedScore> observedScores;

template <class Node>
void recordScore(const Node& node, float heuristic,
                 const std::array<float, 2>& scale) {
  std::vector<size_t> path;
  for (auto ptr = &node; ptr->parent; ptr = ptr->parent.get())
    path.push_back(ptr->option);
  std::ranges::reverse(path);
  observedScores.insert_or_assign(path, ObservedScore{
      path, P::getCost(node), heuristic, scale});
}
auto P::getHeuristic(const std::vector<GateJob>& jobs, float factor,
                     float value, const std::array<float, 2>& scale,
                     const GateNode& node) -> float {
  observedGateJobs = jobs;
  const auto result = originalHeuristic(jobs, factor, value, scale, node);
  recordScore(node, result, scale);
  return result;
}
auto P::getHeuristic(const std::vector<AtomJob>& jobs, float factor,
                     float value, const std::array<float, 2>& scale,
                     const AtomNode& node) -> float {
  observedAtomJobs = jobs;
  const auto result = originalHeuristic(jobs, factor, value, scale, node);
  recordScore(node, result, scale);
  return result;
}
} // namespace na::zoned

using namespace na::zoned;
template <class Range> void numbers(const Range& values) {
  std::cout << '[';
  bool first = true;
  for (const auto value : values) {
    if (!first) std::cout << ',';
    first = false;
    std::cout << +value;
  }
  std::cout << ']';
}
void site(const Site& value) {
  const auto& [slm, row, col] = value;
  std::cout << '[' << slm.get().id << ',' << row << ',' << col << ']';
}
void placement(const Placement& values) {
  std::cout << '[';
  for (size_t i = 0; i < values.size(); ++i) {
    if (i) std::cout << ',';
    site(values[i]);
  }
  std::cout << ']';
}
template <class Range> void pairs(const Range& values) {
  std::cout << '[';
  for (size_t i = 0; i < values.size(); ++i) {
    if (i) std::cout << ',';
    numbers(values[i]);
  }
  std::cout << ']';
}
void gateJobs() {
  std::cout << '[';
  for (size_t i = 0; i < observedGateJobs.size(); ++i) {
    if (i) std::cout << ',';
    const auto& job = observedGateJobs[i];
    std::cout << "{\"atoms\":";
    numbers(job.qubits);
    std::cout << ",\"current\":";
    pairs(job.currentSites);
    std::cout << ",\"mean_lookahead\":" << job.meanLookaheadCost
              << ",\"options\":[";
    for (size_t j = 0; j < job.options.size(); ++j) {
      if (j) std::cout << ',';
      const auto& option = job.options[j];
      std::cout << "{\"sites\":";
      pairs(option.sites);
      std::cout << ",\"distances\":";
      numbers(option.distance);
      std::cout << ",\"lookahead\":" << option.lookaheadCost
                << ",\"reuse\":false}";
    }
    std::cout << "]}";
  }
  std::cout << ']';
}
void atomJobs() {
  std::cout << '[';
  for (size_t i = 0; i < observedAtomJobs.size(); ++i) {
    if (i) std::cout << ',';
    const auto& job = observedAtomJobs[i];
    std::cout << "{\"atoms\":[" << job.atom << "],\"current\":[";
    numbers(job.currentSite);
    std::cout << "],\"mean_lookahead\":" << job.meanLookaheadCost
              << ",\"options\":[";
    for (size_t j = 0; j < job.options.size(); ++j) {
      if (j) std::cout << ',';
      const auto& option = job.options[j];
      std::cout << "{\"sites\":[";
      numbers(option.site);
      std::cout << "],\"distances\":[" << option.distance
                << "],\"lookahead\":" << option.lookaheadCost
                << ",\"reuse\":" << (option.reuse ? "true" : "false") << '}';
    }
    std::cout << "]}";
  }
  std::cout << ']';
}
void scores() {
  std::cout << '[';
  bool first = true;
  for (const auto& [_, score] : observedScores) {
    if (!first) std::cout << ',';
    first = false;
    std::cout << "{\"path\":";
    numbers(score.path);
    std::cout << ",\"cost\":" << score.cost
              << ",\"heuristic\":" << score.heuristic << ",\"scale\":";
    numbers(score.scale);
    std::cout << '}';
  }
  std::cout << ']';
}
int main() {
  size_t count;
  std::cin >> count;
  Architecture arch;
  std::map<int, std::array<SLM, 2>*> paired;
  std::map<int, size_t> pairCounts;
  std::map<size_t, const SLM*> ids;
  for (size_t i = 0; i < count; ++i) {
    SLM value;
    int group;
    std::cin >> value.id >> group >> value.nRows >> value.nCols
             >> value.siteSeparation.first >> value.siteSeparation.second
             >> value.location.first >> value.location.second;
    if (group < 0) {
      arch.storageZones.push_back(std::make_unique<SLM>(value));
      ids[value.id] = arch.storageZones.back().get();
    } else {
      if (!paired.contains(group)) {
        arch.entanglementZones.push_back(
            std::make_unique<std::array<SLM, 2>>());
        paired[group] = arch.entanglementZones.back().get();
      }
      auto& dest = (*paired[group])[pairCounts[group]++];
      dest = value;
      dest.entanglementZone_ = paired[group];
      ids[value.id] = &dest;
    }
  }
  arch.preprocessing();
  P::Config config;
  std::cin >> config.useWindow >> config.windowMinWidth >> config.windowRatio
           >> config.windowShare >> config.deepeningFactor
           >> config.deepeningValue >> config.lookaheadFactor
           >> config.reuseLevel >> config.trials >> config.queueCapacity;
  P placer(arch, config);
  std::cout << std::setprecision(17) << "{\"initial\":";
  std::cin >> count;
  placement(placer.makeInitialPlacement(count));
  std::cout << ",\"nearest_storage\":[";
  std::cin >> count;
  for (size_t i = 0; i < count; ++i) {
    size_t s, r, c;
    std::cin >> s >> r >> c;
    if (i) std::cout << ',';
    site(arch.nearestStorageSite(*ids.at(s), r, c));
  }
  std::cout << "],\"nearest_entanglement\":[";
  std::cin >> count;
  std::vector<double> nearestDistances;
  for (size_t i = 0; i < count; ++i) {
    size_t a, r, c, b, rr, cc;
    std::cin >> a >> r >> c >> b >> rr >> cc;
    if (i) std::cout << ',';
    site(arch.nearestEntanglementSite(*ids.at(a), r, c, *ids.at(b), rr, cc));
    nearestDistances.push_back(arch.nearestEntanglementSiteDistance(
        *ids.at(a), r, c, *ids.at(b), rr, cc));
  }
  std::cout << "],\"nearest_entanglement_distance\":";
  numbers(nearestDistances);
  std::string stage;
  std::cin >> stage;
  Placement previous;
  std::cin >> count;
  for (size_t i = 0; i < count; ++i) {
    size_t s, r, c;
    std::cin >> s >> r >> c;
    previous.emplace_back(*ids.at(s), r, c);
  }
  const auto layer = [] {
    size_t n;
    std::cin >> n;
    TwoQubitGateLayer result(n);
    for (auto& gate : result) std::cin >> gate[0] >> gate[1];
    return result;
  };
  const auto gates = layer();
  const auto nextGates = layer();
  const auto reused = [] {
    size_t n;
    std::cin >> n;
    std::unordered_set<qc::Qubit> result;
    while (n--) {
      size_t value;
      std::cin >> value;
      result.insert(value);
    }
    return result;
  };
  const auto previousReuse = reused();
  const auto nextReuse = reused();
  Placement result;
  if (stage == "gate") {
    result = placer.placeGatesInEntanglementZone(
        previous, previousReuse, gates, nextReuse, nextGates);
  } else if (stage == "storage") {
    result = placer.placeAtomsInStorageZone(
        previous, nextReuse, gates, nextGates);
  }
  std::cout << ",\"jobs\":";
  if (stage == "gate") gateJobs(); else atomJobs();
  std::cout << ",\"scores\":";
  scores();
  std::cout << ",\"final\":";
  placement(result);
  std::cout << "}\n";
}
