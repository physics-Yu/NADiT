int main() {
  using namespace na::zoned;
  std::size_t count;
  double preferSplit;
  std::cin >> count >> preferSplit;
  using Movement = std::tuple<std::size_t, std::size_t, std::size_t, std::size_t>;
  std::vector<Movement> movements;
  std::size_t maxX = 0;
  std::size_t maxY = 0;
  for (std::size_t atom = 0; atom < count; ++atom) {
    std::size_t sx, sy, tx, ty;
    std::cin >> sx >> sy >> tx >> ty;
    movements.emplace_back(sx, sy, tx, ty);
    maxX = std::max({maxX, sx, tx});
    maxY = std::max({maxY, sy, ty});
  }
  if (!std::cin) {
    return 2;
  }
  const SLM plane{{1, 1}, maxY + 1, maxX + 1, {0, 0}};
  Placement source, target;
  for (const auto& [sx, sy, tx, ty] : movements) {
    source.emplace_back(plane, sy, sx);
    target.emplace_back(plane, ty, tx);
  }
  const Architecture architecture;
  const IndependentSetRouter strict(
      architecture, {IndependentSetRouter::Config::Method::STRICT, preferSplit});
  const IndependentSetRouter relaxed(
      architecture, {IndependentSetRouter::Config::Method::RELAXED, preferSplit});
  const auto strictRouting = strict.route({source, target});
  const auto relaxedRouting = relaxed.route({source, target});
  const auto movers = strict.getAtomsToMove(source, target);
  auto writeAtoms = [](const auto& atoms) {
    std::cout << '[';
    bool first = true;
    for (const auto atom : atoms) {
      if (!first) {
        std::cout << ',';
      }
      first = false;
      std::cout << atom;
    }
    std::cout << ']';
  };
  auto writeRouting = [&writeAtoms](const auto& routing) {
    std::cout << '[';
    bool first = true;
    for (const auto& group : routing.at(0)) {
      if (!first) {
        std::cout << ',';
      }
      first = false;
      writeAtoms(group);
    }
    std::cout << ']';
  };
  std::cout << std::setprecision(17) << "{\"mover_order\":";
  writeAtoms(movers);
  std::cout << ",\"pairs\":[";
  bool firstPair = true;
  for (std::size_t i = 0; i < count; ++i) {
    for (std::size_t j = i + 1; j < count; ++j) {
      if (!firstPair) {
        std::cout << ',';
      }
      firstPair = false;
      const auto result = IndependentSetRouter::isRelaxedCompatibleMovement(
          movements[i], movements[j]);
      using Status = IndependentSetRouter::MovementCompatibility::Status;
      const char* status = result.status == Status::StrictlyCompatible
                               ? "strict"
                               : result.status == Status::RelaxedCompatible
                                     ? "relaxed"
                                     : "incompatible";
      std::cout << "{\"atoms\":[" << i << ',' << j << "],\"status\":\""
                << status << "\",\"merge_cost\":";
      if (result.mergeCost.has_value()) {
        std::cout << *result.mergeCost;
      } else {
        std::cout << "null";
      }
      std::cout << '}';
    }
  }
  std::cout << "],\"strict_groups\":";
  writeRouting(strictRouting);
  std::cout << ",\"relaxed_groups\":";
  writeRouting(relaxedRouting);
  std::cout << "}\n";
}
