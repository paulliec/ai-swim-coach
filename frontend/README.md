# SwimCoach AI - Frontend

React frontend for the SwimCoach AI video analysis platform.

## Features

- 📹 **Video Upload**: Select video files from your device
- 🎞️ **Client-Side Frame Extraction**: Automatically extracts ~15 frames using Canvas API
- 👁️ **Preview Thumbnails**: See exactly what frames will be analyzed
- 🏊 **Stroke Selection**: Choose your stroke type (freestyle, backstroke, etc.)
- 🤖 **AI Analysis**: Get detailed coaching feedback from Claude
- 💬 **Interactive Chat**: Ask follow-up questions about your technique
- 🔑 **API Key Management**: Securely stored in localStorage

## Quick Start

### Prerequisites

- Node.js 18+ installed
- Backend API running on `http://localhost:8000`

### Installation

```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

The app will open at **http://localhost:3000**

### Production Build

```bash
# Build for production
npm run build

# Preview production build
npm run preview
```

## Usage

1. **API Key (Optional Setup)**
   - Add `VITE_API_KEY=dev-key-1` to `.env.local` to skip manual entry
   - Otherwise, enter `dev-key-1` in the UI
   - Key is saved to localStorage

2. **Select Video**
   - Click "Choose Video File"
   - Select a swimming video from your device
   - Frames are automatically extracted (takes a few seconds)

3. **Configure Analysis**
   - Select stroke type
   - Optionally add notes about what you want feedback on

4. **Analyze**
   - Click "Analyze My Technique"
   - Frames are uploaded to the API
   - AI analysis begins automatically
   - Results appear in ~10-30 seconds

5. **Chat**
   - Ask follow-up questions
   - Get clarification on drills
   - Request specific advice

## Technical Details

### Frame Extraction

Frames are extracted entirely in the browser using:
- HTML5 `<video>` element for playback
- Canvas API for rendering frames
- Blob API for creating JPEG images

**No video file is uploaded** - only the extracted frames go to the server.

### API Integration

The frontend proxies API calls through Vite's dev server:
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- Requests to `/api/*` are proxied to the backend

### State Management

Simple React state using `useState` hooks. No Redux/MobX needed for this single-page app.

## Project Structure

```
frontend/
├── src/
│   ├── App.jsx          # Main component (video upload, analysis, chat)
│   ├── main.jsx         # React entry point
│   └── index.css        # Tailwind styles
├── index.html           # HTML template
├── package.json         # Dependencies
├── vite.config.js       # Vite configuration (proxy, etc.)
├── tailwind.config.js   # Tailwind CSS config
└── postcss.config.js    # PostCSS config
```

## Customization

### Change Number of Frames

In `App.jsx`, line ~70:

```javascript
const frameCount = 15  // Change this number
```

### Change API URL

In `vite.config.js`, update the proxy target:

```javascript
proxy: {
  '/api': {
    target: 'http://your-api-url:8000',
    changeOrigin: true,
  }
}
```

## Deployment

### Static Hosting (Netlify, Vercel, etc.)

```bash
npm run build
# Deploy the 'dist' folder
```

Update API URL in production:
- Set `VITE_API_URL` environment variable
- Or hardcode in `App.jsx`

### Docker

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build
FROM nginx:alpine
COPY --from=0 /app/dist /usr/share/nginx/html
```

## Troubleshooting

### CORS Errors

Make sure your backend has CORS enabled for the frontend origin:

```python
# In backend src/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Frontend URL
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Frame Extraction Fails

Some video codecs may not work in the browser. Try:
- Converting video to MP4 (H.264)
- Using a smaller video file
- Checking browser console for errors

### API Key Not Working

Verify:
- Backend is running
- API key matches one in backend's `.env` file
- Key is in `X-API-Key` header (check Network tab)

## License

MIT

