import { Calculator } from "./calc";
import { normalize } from "@/util";

export default function main() {
  const c = new Calculator();
  const sum = c.add(1, 2);
  return normalize(`sum=${sum}`);
}
