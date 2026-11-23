// Only a verified zero-voxel finding is eligible for a negative model statement.
export function formatEffusions(lines: string[] = []): string {
  const absent = new Set<string>();
  const remaining: string[] = [];
  for (const line of lines) {
    const match = /^(胸腔积液|心包积液)专项模型技术试验分割结果为0个体素[。.]?$/.exec(line.replace(/\s/g, ""));
    if (match) absent.add(match[1]);
    else remaining.push(line.replace(/^(胸腔积液|心包积液)专项模型技术试验分割结果为\s*(\d+)\s*个体素[。.]?$/, "$1分割区域为 $2 个体素。"));
  }
  const sites = ["胸腔积液", "心包积液"].filter(site => absent.has(site));
  if (sites.length) remaining.unshift("未见" + sites.join("及") + "。");
  return remaining.join("");
}

// Presentation vocabulary for verified findings, including saved reports.
export function clinicalText(text: string): string {
  return text.replace(/模型提示呈/g, "呈")
    .replace(/模型未检出钙化/g, "未见钙化")
    .replace(/模型提示有钙化/g, "可见钙化")
    .replace(/模型未提示钙化/g, "未见钙化");
}
