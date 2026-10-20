import { redirect } from "next/navigation";

/** The v3 address of the resolution review, kept so old links land. */
export default async function Summary({ params }: { params: Promise<{ id: string }> }) {
  redirect(`/chats/${(await params).id}/resolution`);
}
