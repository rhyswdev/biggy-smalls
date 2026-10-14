"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { z } from "zod";

const Theme = z.enum(["system", "light", "dark"]);

/** Saves the chosen theme in a cookie, so the server renders the page in it with no flash. */
export async function setTheme(value: string): Promise<void> {
  const theme = Theme.parse(value);
  (await cookies()).set("smalls-theme", theme, { path: "/", sameSite: "lax", httpOnly: true, maxAge: 60 * 60 * 24 * 365 });
  revalidatePath("/", "layout");
}
