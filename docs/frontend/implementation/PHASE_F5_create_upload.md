# Phase F5 — Create post + media upload (the 3-call orchestration)

**Goal.** Drive `init → direct-to-bucket → confirm → create` with previews,
per-file progress, removal, and an instant post — while handling the many failure
modes of a real upload.

---

## F5.1 api/media.ts

```ts
// src/api/media.ts
import axios from 'axios';
import { api } from './client';

export const initUpload = (content_type: string, size: number) =>
  api.post('/media/upload-init/', { content_type, size }).then(r => r.data);
  // -> { upload_id, url, fields, storage_key }

export function putToBucket(
  presigned: { url: string; fields: Record<string, string> },
  file: File, onProgress: (pct: number) => void, signal: AbortSignal,
) {
  const form = new FormData();
  // S3 requires the policy FIELDS FIRST, then the file LAST.
  Object.entries(presigned.fields).forEach(([k, v]) => form.append(k, v));
  form.append('file', file);
  // Bare axios (NOT the api instance): no auth header, no baseURL, to the bucket.
  return axios.post(presigned.url, form, {
    onUploadProgress: e => onProgress(Math.round((e.loaded / (e.total ?? file.size)) * 100)),
    signal,
  });
}

export const confirmUpload = (uploadId: number) =>
  api.post(`/media/${uploadId}/confirm/`).then(r => r.data);
```

---

## F5.2 Upload draft state (local UI state — reducer)

```ts
type Att = {
  localId: string; file: File; previewUrl: string;
  uploadId?: number; progress: number;
  status: 'uploading' | 'processing' | 'ready' | 'failed';
  error?: string; abort: AbortController;
};
```

```ts
// src/features/media/useUpload.ts
export function useUpload() {
  const [atts, dispatch] = useReducer(reducer, []);

  async function add(files: FileList) {
    for (const file of Array.from(files)) {
      const localId = crypto.randomUUID();
      const abort = new AbortController();
      // client-side pre-check (UX only; backend enforces for real)
      if (!ALLOWED.has(file.type)) { dispatch(fail(localId, 'Unsupported type')); continue; }
      dispatch(addDraft({ localId, file, previewUrl: URL.createObjectURL(file), progress: 0, status: 'uploading', abort }));
      try {
        const { upload_id, url, fields } = await initUpload(file.type, file.size);
        dispatch(setUploadId(localId, upload_id));
        await putToBucket({ url, fields }, file, p => dispatch(setProgress(localId, p)), abort.signal);
        dispatch(setStatus(localId, 'processing'));
        await confirmUpload(upload_id);
        dispatch(setStatus(localId, 'ready'));
      } catch (e) {
        if (!abort.signal.aborted) dispatch(fail(localId, toMessage(e)));
      }
    }
  }

  function remove(localId: string) {
    const a = atts.find(x => x.localId === localId);
    a?.abort.abort();
    if (a) URL.revokeObjectURL(a.previewUrl);   // free memory
    dispatch(removeDraft(localId));
  }

  return { atts, add, remove, allReady: atts.length > 0 && atts.every(a => a.status === 'ready') };
}
```

---

## F5.3 Create post + optimistic prepend

```ts
// src/features/posts/useCreatePost.ts
export function useCreatePost() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreatePostBody) => createPost(body),
    onSuccess: (post) => {
      qc.setQueryData(queryKeys.post(post.id), post);
      qc.setQueryData(queryKeys.feed(), (old: any) => old && ({
        ...old,
        pages: [{ ...old.pages[0], results: [post, ...old.pages[0].results] }, ...old.pages.slice(1)],
      }));
    },
  });
}
```

`PostComposer`: caption field + `UploadTile` grid; **"Post" disabled until
`allReady`**; on submit send `media_ids = atts.map(a => a.uploadId!)` in display
order; revoke object URLs and reset on success.

---

## Edge cases (reviewed)

- **Multipart field order.** S3 presigned POST requires the policy **fields first,
  then `file` last**, or S3 rejects it. `putToBucket` appends in that order.
- **Bucket CORS.** The direct upload is cross-origin (browser → S3). The bucket
  must allow the frontend origin (`PUT/POST`, the needed headers). Without it you
  get an opaque CORS error and zero progress. **Infra/backend follow-up**, but the
  frontend symptom is upload failing immediately — document it.
- **Use bare axios for the bucket PUT**, not the `api` instance — you don't want
  the `Authorization` header or `baseURL` sent to S3.
- **Upload timeout.** A 200MB video needs a long (or disabled) timeout; don't
  inherit a short global axios timeout for the bucket request.
- **Remove mid-flight.** `AbortController.abort()` cancels the in-flight upload;
  the orphaned `pending` media is cleaned by the backend purge job — no client
  delete needed. Guard the catch with `signal.aborted` so an abort isn't shown as
  an error.
- **Confirm → failed (moderation reject / oversize).** Surface a per-tile error and
  block posting that file; let the user remove/retry.
- **`ready` gate is functional, not cosmetic.** `create_post` rejects non-ready /
  unowned / already-attached media (403). Disabling "Post" until `allReady`
  prevents that.
- **Navigation away with uploads in progress.** Add a `beforeunload` warning and an
  in-app confirm if there are `uploading/processing` attachments or an unsaved
  caption.
- **Memory leaks.** `URL.revokeObjectURL` on remove and on unmount; object URLs
  pin the file in memory otherwise.
- **Video preview.** `createObjectURL` works for `<video>` too; render a `<video>`
  tile for video types.
- **Carousel order.** `media_ids` order == tile order == backend `position`. Let the
  user reorder tiles if desired; send in that order.
- **Duplicate submit.** Disable "Post" while the create mutation is pending; the
  optimistic prepend + reconcile avoids a flicker.
- **Partial failure.** If one of several files fails, don't block the others; let
  the user remove the failed tile and post the rest.

## Definition of done

Files preview instantly, upload in the background with progress, can be removed
mid-flight, and "Post" (enabled only when all ready) creates a post that appears
at the top of the feed immediately. All the failure modes above are handled.
