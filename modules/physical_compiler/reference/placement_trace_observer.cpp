// Observation only: node identity, queue decisions, and score components.
template <class Node> void traceNode(const char* kind, const Node& node,
                                    double priority) {
  std::vector<size_t> path;
  for (auto ptr=&node;ptr->parent;ptr=ptr->parent.get()) path.push_back(ptr->option);
  std::ranges::reverse(path);
  std::cerr << std::setprecision(17) << "{\"kind\":\"" << kind << "\",\"path\":[";
  for (size_t i=0;i<path.size();++i) {
    if (i) std::cerr << ',';
    std::cerr << path[i];
  }
  std::cerr << "],\"priority\":" << priority << "}\n";
}
template <class Node> void traceScore(const Node& node,double cost,double heuristic,
    const std::array<float,2>& scale,double deviation) {
  std::vector<size_t> path;
  for (auto ptr=&node;ptr->parent;ptr=ptr->parent.get()) path.push_back(ptr->option);
  std::ranges::reverse(path);
  std::cerr << std::setprecision(17) << "{\"kind\":\"score\",\"path\":[";
  for (size_t i=0;i<path.size();++i) {
    if (i) std::cerr << ',';
    std::cerr << path[i];
  }
  std::cerr << "],\"cost\":" << cost << ",\"heuristic\":" << heuristic
    << ",\"scale\":[" << scale[0] << ',' << scale[1]
    << "],\"deviation\":" << deviation << ",\"lookahead\":" << node.lookaheadCost
    << ",\"max_distances\":[";
  for (size_t i=0;i<node.maxDistancesOfPlacedAtomsPerGroup.size();++i) {
    if (i) std::cerr << ',';
    std::cerr << node.maxDistancesOfPlacedAtomsPerGroup[i];
  }
  std::cerr << "],\"groups\":[";
  for (size_t g=0;g<node.groups.size();++g) {
    if (g) std::cerr << ',';
    std::cerr << '[';
    for (size_t axis=0;axis<2;++axis) {
      if (axis) std::cerr << ',';
      std::cerr << '[';
      bool first=true;
      for (const auto& [key,value] : node.groups[g][axis]) {
        if (!first) std::cerr << ',';
        first=false;
        std::cerr << '[' << +key << ',' << +value << ']';
      }
      std::cerr << ']';
    }
    std::cerr << ']';
  }
  std::cerr << "]}\n";
}
