// Serialize original stage outputs without modifying scheduling or decisions.
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
template <class Range> void nestedNumbers(const Range& values) {
  std::cout << '[';
  bool first = true;
  for (const auto& row : values) {
    if (!first) std::cout << ',';
    first = false;
    numbers(row);
  }
  std::cout << ']';
}
void positions(const std::vector<Location>& values) {
  std::cout << '[';
  for (size_t i=0;i<values.size();++i) {
    if (i) std::cout << ',';
    std::cout << '[' << values[i].x << ',' << values[i].y << ']';
  }
  std::cout << ']';
}
void recordStage(const char* stage, auto writer) {
  std::ostringstream output;
  auto* previous=std::cout.rdbuf(output.rdbuf());
  std::cout << "{\"stage\":\"" << stage << "\",\"result\":";
  writer();
  std::cout << '}';
  std::cout.rdbuf(previous);
  std::cerr << "TRACE " << output.str() << '\n';
}
void writePlacements(const std::vector<Placement>& values) {
  std::cout << '[';
  for (size_t i=0;i<values.size();++i) {
    if (i) std::cout << ',';
    std::cout << '[';
    for (size_t q=0;q<values[i].size();++q) {
      if (q) std::cout << ',';
      const auto& [slm,r,c]=values[i][q];
      std::cout << '[' << slm.get().id << ',' << r << ',' << c << ']';
    }
    std::cout << ']';
  }
  std::cout << ']';
}
auto readArchitecture() -> Architecture {
  size_t count;
  std::cin >> count;
  Architecture arch;
  std::map<int, std::array<SLM,2>*> paired;
  std::map<int,size_t> pairCounts;
  for (size_t i=0;i<count;++i) {
    SLM value;
    int group;
    std::cin >> value.id >> group >> value.nRows >> value.nCols
             >> value.siteSeparation.first >> value.siteSeparation.second
             >> value.location.first >> value.location.second;
    if (group < 0) {
      arch.storageZones.push_back(std::make_unique<SLM>(value));
    } else {
      if (!paired.contains(group)) {
        arch.entanglementZones.push_back(
            std::make_unique<std::array<SLM,2>>());
        paired[group] = arch.entanglementZones.back().get();
      }
      auto& dest = (*paired[group])[pairCounts[group]++];
      dest = value;
      dest.entanglementZone_ = paired[group];
    }
  }
  size_t rangeCount;
  std::cin >> rangeCount;
  for (size_t i=0;i<rangeCount;++i) {
    size_t minX,minY,maxX,maxY;
    std::cin >> minX >> minY >> maxX >> maxY;
    arch.rydbergRangeMinX.push_back(minX);
    arch.rydbergRangeMinY.push_back(minY);
    arch.rydbergRangeMaxX.push_back(maxX);
    arch.rydbergRangeMaxY.push_back(maxY);
  }
  if (rangeCount==0) for (const auto& zone : arch.entanglementZones) {
    const auto& left = zone->front();
    const auto& right = zone->back();
    arch.rydbergRangeMinX.push_back(std::min(left.location.first,right.location.first));
    arch.rydbergRangeMinY.push_back(std::min(left.location.second,right.location.second));
    arch.rydbergRangeMaxX.push_back(std::max(
        left.location.first+(left.nCols-1)*left.siteSeparation.first,
        right.location.first+(right.nCols-1)*right.siteSeparation.first));
    arch.rydbergRangeMaxY.push_back(std::max(
        left.location.second+(left.nRows-1)*left.siteSeparation.second,
        right.location.second+(right.nRows-1)*right.siteSeparation.second));
  }
  arch.preprocessing();
  return arch;
}
auto readCircuit() -> qc::QuantumComputation {
  qc::QuantumComputation circuit;
  size_t count;
  std::cin >> circuit.nQubits >> count;
  const std::map<std::string,qc::OpType> names = {
    {"Z",qc::Z},{"RZ",qc::RZ},{"RY",qc::RY},{"RX",qc::RX},{"U",qc::U},
    {"U2",qc::U2},{"P",qc::P},{"S",qc::S},{"Sdg",qc::Sdg},{"T",qc::T},
    {"Tdg",qc::Tdg},{"H",qc::H},{"X",qc::X},{"Y",qc::Y},{"V",qc::V},
    {"Vdg",qc::Vdg},{"SX",qc::SX},{"SXdg",qc::SXdg},{"Barrier",qc::Barrier}};
  for (size_t index=0;index<count;++index) {
    std::string type;
    size_t length;
    std::cin >> type >> length;
    auto op = std::make_unique<qc::StandardOperation>();
    op->inputIndex=index;
    op->targets.resize(length);
    for (auto& q : op->targets) std::cin >> q;
    std::cin >> length;
    op->parameters.resize(length);
    for (auto& parameter : op->parameters) std::cin >> parameter;
    if (type == "CZ") {
      op->type=qc::Z;
      op->controls.push_back({op->targets.front()});
      op->targets.erase(op->targets.begin());
    } else {
      op->type=names.at(type);
    }
    circuit.operations.push_back(std::move(op));
  }
  return circuit;
}
int main() {
  try {
    using Clock = std::chrono::steady_clock;
    const auto beforeArchitecture=Clock::now();
    auto arch = readArchitecture();
    const auto afterArchitecture=Clock::now();
    ASAPScheduler::Config scheduleConfig;
    HeuristicPlacer::Config placementConfig;
    IndependentSetRouter::Config routingConfig;
    std::string method,routing;
    std::cin >> scheduleConfig.maxFillingFactor >> method >> routing
             >> placementConfig.useWindow >> placementConfig.windowMinWidth
             >> placementConfig.windowRatio >> placementConfig.windowShare
             >> placementConfig.deepeningFactor >> placementConfig.deepeningValue
             >> placementConfig.lookaheadFactor >> placementConfig.reuseLevel
             >> placementConfig.trials >> placementConfig.queueCapacity
             >> routingConfig.preferSplit >> placementConfig.maxNodes;
    placementConfig.method = method=="astar"
      ? HeuristicPlacer::Config::Method::ASTAR : HeuristicPlacer::Config::Method::IDS;
    routingConfig.method = routing=="strict"
      ? IndependentSetRouter::Config::Method::STRICT
      : IndependentSetRouter::Config::Method::RELAXED;
    const auto circuit=readCircuit();
    // Identical ordered stage calls to the pinned Compiler and synthesizer.
    const auto beforeScheduling=Clock::now();
    const auto [singles,doubles]=ASAPScheduler(arch,scheduleConfig).schedule(circuit);
    const auto afterScheduling=Clock::now();
    recordStage("scheduling",[&] {
      std::cout << "{\"single_layers\":[";
      for (size_t i=0;i<singles.size();++i) {
        if (i) std::cout << ',';
        std::vector<size_t> indices;
        for (const auto& op : singles[i]) indices.push_back(op.get().inputIndex);
        numbers(indices);
      }
      std::cout << "],\"two_layers\":[";
      for (size_t i=0;i<doubles.size();++i) {
        if (i) std::cout << ',';
        nestedNumbers(doubles[i]);
      }
      std::cout << "]}";
    });
    const auto beforeReuse=Clock::now();
    const auto reuse=VertexMatchingReuseAnalyzer(arch,{}).analyzeReuse(doubles);
    const auto afterReuse=Clock::now();
    recordStage("reuse",[&] {
      std::cout << '[';
      for (size_t i=0;i<reuse.size();++i) {
        if (i) std::cout << ',';
        auto ordered=std::vector<size_t>(reuse[i].begin(),reuse[i].end());
        std::ranges::sort(ordered);
        numbers(ordered);
      }
      std::cout << ']';
    });
    const auto beforePlacement=Clock::now();
    const auto placement=HeuristicPlacer(arch,placementConfig).place(
        circuit.nQubits,doubles,reuse);
    const auto afterPlacement=Clock::now();
    recordStage("placement",[&] { writePlacements(placement); });
    const auto beforeRouting=Clock::now();
    const auto routed=IndependentSetRouter(arch,routingConfig).route(placement);
    const auto afterRouting=Clock::now();
    recordStage("routing",[&] {
      std::cout << '[';
      for (size_t i=0;i<routed.size();++i) {
        if (i) std::cout << ',';
        nestedNumbers(routed[i]);
      }
      std::cout << ']';
    });
    const auto beforeCodegen=Clock::now();
    const auto code=CodeGenerator(arch,{}).generate(singles,placement,routed);
    const auto afterCodegen=Clock::now();
    recordStage("codegen",[&] { std::cout << code.events.size(); });
    std::cout << std::setprecision(17) << "{\"single_layers\":[";
    for (size_t i=0;i<singles.size();++i) {
      if (i) std::cout << ',';
      std::vector<size_t> indices;
      for (const auto& op : singles[i]) indices.push_back(op.get().inputIndex);
      numbers(indices);
    }
    std::cout << "],\"two_layers\":[";
    for (size_t i=0;i<doubles.size();++i) {
      if (i) std::cout << ',';
      nestedNumbers(doubles[i]);
    }
    std::cout << "],\"reuse\":[";
    for (size_t i=0;i<reuse.size();++i) {
      if (i) std::cout << ',';
      auto ordered=std::vector<size_t>(reuse[i].begin(),reuse[i].end());
      std::ranges::sort(ordered);
      numbers(ordered);
    }
    std::cout << "],\"placements\":[";
    for (size_t i=0;i<placement.size();++i) {
      if (i) std::cout << ',';
      std::cout << '[';
      for (size_t q=0;q<placement[i].size();++q) {
        if (q) std::cout << ',';
        const auto& [slm,r,c]=placement[i][q];
        std::cout << '[' << slm.get().id << ',' << r << ',' << c << ']';
      }
      std::cout << ']';
    }
    std::cout << "],\"routings\":[";
    for (size_t i=0;i<routed.size();++i) {
      if (i) std::cout << ',';
      nestedNumbers(routed[i]);
    }
    std::cout << "],\"zones\":[";
    for (size_t i=0;i<code.zones.size();++i) {
      if (i) std::cout << ',';
      const auto& z=code.zones[i];
      std::cout << "{\"name\":\"" << z.name << "\",\"extent\":["
                << z.extent.minX << ',' << z.extent.minY << ','
                << z.extent.maxX << ',' << z.extent.maxY << "]}";
    }
    std::cout << "],\"initial\":";
    positions(code.initial);
    std::cout << ",\"actions\":[";
    for (size_t i=0;i<code.events.size();++i) {
      if (i) std::cout << ',';
      const auto& e=code.events[i];
      std::cout << "{\"kind\":\"" << e.kind << "\",\"atoms\":";
      numbers(e.atoms);
      std::cout << ",\"targets\":";
      positions(e.targets);
      std::cout << ",\"parameters\":";
      numbers(e.parameters);
      std::cout << ",\"zones\":";
      numbers(e.zones);
      std::cout << '}';
    }
    const auto elapsed=[](auto start,auto end) {
      return std::chrono::duration<double>(end-start).count();
    };
    std::cout << "],\"timings\":{"
              << "\"architecture_s\":" << elapsed(beforeArchitecture,afterArchitecture)
              << ",\"scheduling_s\":" << elapsed(beforeScheduling,afterScheduling)
              << ",\"reuse_s\":" << elapsed(beforeReuse,afterReuse)
              << ",\"placement_s\":" << elapsed(beforePlacement,afterPlacement)
              << ",\"routing_s\":" << elapsed(beforeRouting,afterRouting)
              << ",\"codegen_s\":" << elapsed(beforeCodegen,afterCodegen)
              << "}}\n";
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 2;
  }
}
